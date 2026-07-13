/* areas — page script (extracted from inline <script>). */
    function areasData() {
        return {
            areas: [],
            loading: true,
            totalSales: 0,
            totalDebt: 0,
            totalSalesCount: 0,
            totalCustomers: 0,
            chart: null,
            dateFrom: '',
            dateTo: '',
            dateFromDisplay: '',
            dateToDisplay: '',
            
            // Filters
            showFiltersPanel: false,
            divisions: [],
            groups: [],
            selectedDivisions: [],
            selectedSalesGroups: [],  // Группы для продаж
            selectedDebtGroups: [],   // Группы для долгов
            selectedGroups: [],       // Группы для общего использования
            divisionSearch: '',
            salesGroupSearch: '',
            debtGroupSearch: '',
            
            // Modal для детальной информации
            selectedArea: null,
            areaChart: null,
            salesPaymentsChart: null,
            debtChart: null,
            customersHistoryChart: null,

            // Route Stats
            routeStats: {
                planned: 0,
                visited: 0,
                missed: 0,
                unplanned: 0,
                ordered: 0
            },
            routeStatsLoading: false,
            routeStatsLoaded: false,

            // Unpaid Documents
            unpaidDocuments: [],
            unpaidDocumentsFiltered: [],
            unpaidTotalDebt: 0,
            unpaidDocumentsLoading: false,
            unpaidDateFrom: '',
            unpaidDateTo: '',
            unpaidFilterCustomerCode: '',
            unpaidFilterCustomerName: '',
            unpaidFilterMinDebt: 0,

            init() {
                // Инициализация дат: текущий месяц (с 1-го числа до сегодня)
                const today = new Date();
                const firstDayOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
                
                this.dateFrom = this.formatDate(firstDayOfMonth);
                this.dateTo = this.formatDate(today);
                this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                
                // Инициализация дат для неоплаченных документов
                this.unpaidDateFrom = this.dateFrom;
                this.unpaidDateTo = this.dateTo;
                
                this.loadSavedFilters();
                this.loadDivisions();
                this.loadGroups();
                this.loadAreas();
            },

            // Date Helpers
            formatDate(date) {
                const d = new Date(date);
                let month = '' + (d.getMonth() + 1);
                let day = '' + d.getDate();
                const year = d.getFullYear();

                if (month.length < 2) month = '0' + month;
                if (day.length < 2) day = '0' + day;

                return [year, month, day].join('-');
            },

            formatDateForDisplay(isoDate) {
                if (!isoDate) return '';
                const [year, month, day] = isoDate.split('-');
                return `${day}.${month}.${year}`;
            },

            parseDateDisplay(displayStr) {
                // Преобразует DD.MM.YYYY в YYYY-MM-DD
                if (!displayStr) return '';
                const cleaned = displayStr.replace(/[^\d.]/g, '');
                const parts = cleaned.split('.');
                if (parts.length === 3 && parts[0].length <= 2 && parts[1].length <= 2 && parts[2].length === 4) {
                    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
                }
                return '';
            },

            updateDateFrom(value) {
                // Сохраняем позицию курсора
                const input = event.target;
                const cursorPos = input.selectionStart;
                
                // Удаляем все нецифровые символы
                let cleaned = value.replace(/[^\d]/g, '');
                
                // Ограничиваем длину
                cleaned = cleaned.slice(0, 8);
                
                // Добавляем точки автоматически
                let formatted = '';
                for (let i = 0; i < cleaned.length; i++) {
                    if (i === 2 || i === 4) {
                        formatted += '.';
                    }
                    formatted += cleaned[i];
                }
                
                this.dateFromDisplay = formatted;
                
                // Восстанавливаем курсор с учетом добавленных точек
                this.$nextTick(() => {
                    let newPos = cursorPos;
                    if (cursorPos === 2 || cursorPos === 5) {
                        newPos = cursorPos + 1;
                    }
                    input.setSelectionRange(newPos, newPos);
                });
                
                // Если дата полная (DD.MM.YYYY), проверяем и загружаем
                if (formatted.length === 10) {
                    const parsed = this.parseDateDisplay(formatted);
                    if (parsed && this.isValidDate(parsed)) {
                        this.dateFrom = parsed;
                        this.loadAreas();
                    }
                }
            },

            updateDateTo(value) {
                // Сохраняем позицию курсора
                const input = event.target;
                const cursorPos = input.selectionStart;
                
                // Удаляем все нецифровые символы
                let cleaned = value.replace(/[^\d]/g, '');
                
                // Ограничиваем длину
                cleaned = cleaned.slice(0, 8);
                
                // Добавляем точки автоматически
                let formatted = '';
                for (let i = 0; i < cleaned.length; i++) {
                    if (i === 2 || i === 4) {
                        formatted += '.';
                    }
                    formatted += cleaned[i];
                }
                
                this.dateToDisplay = formatted;
                
                // Восстанавливаем курсор с учетом добавленных точек
                this.$nextTick(() => {
                    let newPos = cursorPos;
                    if (cursorPos === 2 || cursorPos === 5) {
                        newPos = cursorPos + 1;
                    }
                    input.setSelectionRange(newPos, newPos);
                });
                
                // Если дата полная (DD.MM.YYYY), проверяем и загружаем
                if (formatted.length === 10) {
                    const parsed = this.parseDateDisplay(formatted);
                    if (parsed && this.isValidDate(parsed)) {
                        this.dateTo = parsed;
                        this.loadAreas();
                    }
                }
            },

            isValidDate(dateStr) {
                const date = new Date(dateStr);
                return date instanceof Date && !isNaN(date);
            },

            setToday() {
                const today = new Date();
                this.dateFrom = this.formatDate(today);
                this.dateTo = this.formatDate(today);
                this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                this.loadAreas();
            },

            setYesterday() {
                const yesterday = new Date();
                yesterday.setDate(yesterday.getDate() - 1);
                this.dateFrom = this.formatDate(yesterday);
                this.dateTo = this.formatDate(yesterday);
                this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                this.loadAreas();
            },

            setThisMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                this.loadAreas();
            },

            setLastMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth(), 0);
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.dateFromDisplay = this.formatDateForDisplay(this.dateFrom);
                this.dateToDisplay = this.formatDateForDisplay(this.dateTo);
                this.loadAreas();
            },

            loadSavedFilters() {
                try {
                    const savedDivisions = localStorage.getItem('areas_selectedDivisions');
                    const savedSalesGroups = localStorage.getItem('areas_selectedSalesGroups');
                    const savedDebtGroups = localStorage.getItem('areas_selectedDebtGroups');
                    
                    if (savedDivisions) {
                        this.selectedDivisions = JSON.parse(savedDivisions);
                    }
                    if (savedSalesGroups) {
                        this.selectedSalesGroups = JSON.parse(savedSalesGroups);
                    }
                    if (savedDebtGroups) {
                        this.selectedDebtGroups = JSON.parse(savedDebtGroups);
                    }
                } catch (error) {
                    console.error('Error loading saved filters:', error);
                }
            },

            saveDivisionsToStorage() {
                try {
                    localStorage.setItem('areas_selectedDivisions', JSON.stringify(this.selectedDivisions));
                } catch (error) {
                    console.error('Error saving divisions:', error);
                }
            },

            saveSalesGroupsToStorage() {
                try {
                    localStorage.setItem('areas_selectedSalesGroups', JSON.stringify(this.selectedSalesGroups));
                } catch (error) {
                    console.error('Error saving sales groups:', error);
                }
            },

            saveDebtGroupsToStorage() {
                try {
                    localStorage.setItem('areas_selectedDebtGroups', JSON.stringify(this.selectedDebtGroups));
                } catch (error) {
                    console.error('Error saving debt groups:', error);
                }
            },

            async loadDivisions() {
                try {
                    const response = await fetch('/api/settings/product-groups');
                    const result = await response.json();
                    if (result.success) {
                        this.divisions = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки дивизионов:', error);
                }
            },

            async loadGroups() {
                try {
                    const response = await fetch('/api/settings/groups');
                    const result = await response.json();
                    if (result.success) {
                        this.groups = result.data;
                    }
                } catch (error) {
                    console.error('Ошибка загрузки групп:', error);
                }
            },

            filteredDivisions() {
                if (!this.divisionSearch) return this.divisions;
                const search = this.divisionSearch.toLowerCase();
                return this.divisions.filter(d => 
                    d.fGROUP.toLowerCase().includes(search) || 
                    d.name.toLowerCase().includes(search)
                );
            },

            filteredSalesGroups() {
                if (!this.salesGroupSearch) return this.groups;
                const search = this.salesGroupSearch.toLowerCase();
                return this.groups.filter(g => 
                    g.code.toLowerCase().includes(search) || 
                    g.name.toLowerCase().includes(search)
                );
            },

            filteredDebtGroups() {
                if (!this.debtGroupSearch) return this.groups;
                const search = this.debtGroupSearch.toLowerCase();
                return this.groups.filter(g => 
                    g.code.toLowerCase().includes(search) || 
                    g.name.toLowerCase().includes(search)
                );
            },

            groupListByParent(list) {
                const groups = {};
                list.forEach(g => {
                    let parentLabel = g.parent_name ? `${g.parent_name}` : (g.parent_code ? g.parent_code : 'Прочие');
                    
                    if (!groups[parentLabel]) {
                        groups[parentLabel] = [];
                    }
                    groups[parentLabel].push(g);
                });
                
                return Object.keys(groups).sort().map(key => ({
                    parent: key,
                    items: groups[key]
                }));
            },

            selectAllDivisions() {
                this.selectedDivisions = this.divisions.map(d => d.fGROUP);
                this.saveDivisionsToStorage();
                this.loadAreas();
            },

            selectAllSalesGroups() {
                this.selectedSalesGroups = this.groups.map(g => g.code);
                this.saveSalesGroupsToStorage();
                this.loadAreas();
            },

            selectAllDebtGroups() {
                this.selectedDebtGroups = this.groups.map(g => g.code);
                this.saveDebtGroupsToStorage();
                this.loadAreas();
            },

            clearFilters() {
                this.selectedDivisions = [];
                this.selectedSalesGroups = [];
                this.selectedDebtGroups = [];
                this.saveDivisionsToStorage();
                this.saveSalesGroupsToStorage();
                this.saveDebtGroupsToStorage();
                this.loadAreas();
            },

            async loadAreas() {
                this.loading = true;
                try {
                    const params = new URLSearchParams({
                        date_from: this.dateFrom,
                        date_to: this.dateTo
                    });
                    
                    if (this.selectedDivisions.length > 0) {
                        params.append('divisions', this.selectedDivisions.join(','));
                    }
                    if (this.selectedSalesGroups.length > 0) {
                        params.append('sales_groups', this.selectedSalesGroups.join(','));
                    }
                    if (this.selectedDebtGroups.length > 0) {
                        params.append('groups', this.selectedDebtGroups.join(','));
                    }
                    
                    const response = await fetch(`/api/sales-areas?${params}`);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.areas = result.data;
                        
                        // Загрузить планы для текущего месяца
                        await this.loadPlans();
                        
                        // Подсчитать итоги
                        this.totalSales = this.areas.reduce((sum, a) => sum + a.TotalSales, 0);
                        this.totalDebt = this.areas.reduce((sum, a) => sum + a.Debt, 0);
                        this.totalSalesCount = this.areas.reduce((sum, a) => sum + a.SalesCount, 0);
                        this.totalCustomers = this.areas.reduce((sum, a) => sum + a.CustomerCount, 0);
                        
                        // Отрисовать график
                        this.$nextTick(() => {
                            this.renderChart();
                        });
                        
                        // Загрузить данные о посещениях для всех территорий
                        await this.loadRouteStatsForAll();
                    }
                } catch (error) {
                    console.error('Ошибка загрузки территорий:', error);
                } finally {
                    this.loading = false;
                }
            },

            async loadRouteStatsForAll() {
                try {
                    const params = new URLSearchParams({
                        date_from: this.dateFrom,
                        date_to: this.dateTo
                    });
                    
                    // Загрузить статистику для каждой территории
                    const promises = this.areas.map(async (area) => {
                        try {
                            const response = await fetch(`/api/sales-areas/${area.code}/route-stats?${params}`);
                            const result = await response.json();
                            if (result.success) {
                                area.routeStats = result.data;
                            } else {
                                area.routeStats = { planned: 0, visited: 0, missed: 0, unplanned: 0, ordered: 0 };
                            }
                        } catch (error) {
                            console.error(`Error loading route stats for area ${area.code}:`, error);
                            area.routeStats = { planned: 0, visited: 0, missed: 0, unplanned: 0, ordered: 0 };
                        }
                    });
                    
                    await Promise.all(promises);
                } catch (error) {
                    console.error('Error loading route stats for all areas:', error);
                }
            },

            async loadPlans() {
                try {
                    // Получить текущий месяц и год из dateFrom
                    const date = new Date(this.dateFrom);
                    const month = date.getMonth() + 1; // JavaScript месяцы 0-11
                    const year = date.getFullYear();
                    
                    const planParams = new URLSearchParams({
                        month: month,
                        year: year
                    });
                    
                    // Добавить группы если выбраны
                    if (this.selectedDebtGroups.length > 0) {
                        planParams.append('groups', this.selectedDebtGroups.join(','));
                    }
                    
                    const planResponse = await fetch(`/api/generate-plans?${planParams}`);
                    const planResult = await planResponse.json();
                    
                    if (planResult.success && planResult.data) {
                        // Добавить планы к каждой территории
                        this.areas.forEach(area => {
                            const planData = planResult.data[area.code];
                            if (planData) {
                                area.MonthlyPlan = planData.sales || 0;
                                area.PlanCredit = planData.credit || 0;
                            } else {
                                area.MonthlyPlan = 0;
                                area.PlanCredit = 0;
                            }
                        });
                    }
                } catch (error) {
                    console.error('Ошибка загрузки планов:', error);
                    // Если ошибка, установить планы в 0
                    this.areas.forEach(area => {
                        area.MonthlyPlan = 0;
                        area.PlanCredit = 0;
                    });
                }
            },

            calculateShare(sales) {
                return this.totalSales > 0 ? (sales / this.totalSales * 100) : 0;
            },

            getColorByRank(index) {
                const colors = ['warning', 'info', 'success', 'primary', 'secondary'];
                return colors[Math.min(index, colors.length - 1)];
            },

            openAreaDetails(area) {
                this.selectedArea = area;
                this.routeStatsLoaded = false; // Reset route stats loaded flag
                this.routeStats = {
                    planned: 0,
                    visited: 0,
                    missed: 0,
                    unplanned: 0,
                    ordered: 0
                };
                const modal = new bootstrap.Modal(document.getElementById('areaDetailsModal'));
                modal.show();
                
                // Render charts after a short delay to ensure modal is fully visible
                setTimeout(() => {
                    // Check which tab is active and render accordingly
                    const activeTab = document.querySelector('#areaDetailsTab .nav-link.active');
                    if (activeTab && activeTab.id === 'dynamics-tab') {
                        this.renderMonthlyHistoryCharts();
                        this.renderAreaComparisonChart();
                    }
                }, 300);
            },
            
            closeAreaDetails() {
                const modal = bootstrap.Modal.getInstance(document.getElementById('areaDetailsModal'));
                if (modal) {
                    modal.hide();
                }
                this.selectedArea = null;
                if (this.areaChart) {
                    this.areaChart.destroy();
                    this.areaChart = null;
                }
                if (this.salesPaymentsChart) {
                    this.salesPaymentsChart.destroy();
                    this.salesPaymentsChart = null;
                }
                if (this.debtChart) {
                    this.debtChart.destroy();
                    this.debtChart = null;
                }
                if (this.customersHistoryChart) {
                    this.customersHistoryChart.destroy();
                    this.customersHistoryChart = null;
                }
            },
            
            viewCustomers(area) {
                // Перейти на страницу клиентов из модального окна
                const params = new URLSearchParams({
                    sales_area: area.code,
                    date_from: this.dateFrom,
                    date_to: this.dateTo
                });
                
                if (this.selectedDivisions.length > 0) {
                    params.append('divisions', this.selectedDivisions.join(','));
                }
                if (this.selectedGroups.length > 0) {
                    params.append('groups', this.selectedGroups.join(','));
                }
                
                window.location.href = `/customers-grid?${params}`;
            },

            async loadRouteStats() {
                if (!this.selectedArea) return;
                
                this.routeStatsLoading = true;
                this.routeStatsLoaded = false;
                this.routeStats = {
                    planned: 0,
                    visited: 0,
                    missed: 0,
                    unplanned: 0,
                    ordered: 0
                };
                
                try {
                    const params = new URLSearchParams({
                        date_from: this.dateFrom,
                        date_to: this.dateTo
                    });
                    
                    const response = await fetch(`/api/sales-areas/${this.selectedArea.code}/route-stats?${params}`);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.routeStats = result.data;
                        this.routeStatsLoaded = true;
                    }
                } catch (error) {
                    console.error('Error loading route stats:', error);
                } finally {
                    this.routeStatsLoading = false;
                }
            },

            async loadUnpaidDocuments() {
                if (!this.selectedArea) return;
                
                this.unpaidDocumentsLoading = true;
                this.unpaidDocuments = [];
                this.unpaidTotalDebt = 0;
                
                try {
                    const params = new URLSearchParams({
                        date_from: this.unpaidDateFrom || this.dateFrom,
                        date_to: this.unpaidDateTo || this.dateTo
                    });
                    
                    // Добавить фильтр по группам клиентов (используем selectedDebtGroups для долгов)
                    const groupsToUse = this.selectedDebtGroups.length > 0 ? this.selectedDebtGroups : this.selectedGroups;
                    if (groupsToUse.length > 0) {
                        params.append('groups', groupsToUse.join(','));
                    }
                    
                    const response = await fetch(`/api/sales-areas/${this.selectedArea.code}/unpaid-documents?${params}`);
                    const result = await response.json();
                    
                    console.log('Unpaid documents API response:', result);
                    console.log('Data length:', result.data ? result.data.length : 0);
                    
                    if (result.success && result.data) {
                        this.unpaidDocuments = result.data;
                        this.unpaidTotalDebt = result.total_debt || 0;
                        console.log('Set unpaidDocuments:', this.unpaidDocuments.length, 'customers');
                        console.log('First customer structure:', JSON.stringify(this.unpaidDocuments[0], null, 2));
                        this.filterUnpaidDocuments();
                    } else {
                        console.error('API returned error or no data:', result);
                        this.unpaidDocuments = [];
                        this.unpaidDocumentsFiltered = [];
                        this.unpaidTotalDebt = 0;
                    }
                } catch (error) {
                    console.error('Error loading unpaid documents:', error);
                    this.unpaidDocuments = [];
                    this.unpaidDocumentsFiltered = [];
                    this.unpaidTotalDebt = 0;
                } finally {
                    this.unpaidDocumentsLoading = false;
                }
            },

            filterUnpaidDocuments() {
                if (!this.unpaidDocuments || this.unpaidDocuments.length === 0) {
                    this.unpaidDocumentsFiltered = [];
                    return;
                }

                const codeFilter = this.unpaidFilterCustomerCode.toLowerCase().trim();
                const nameFilter = this.unpaidFilterCustomerName.toLowerCase().trim();
                const minDebt = this.unpaidFilterMinDebt || 0;

                this.unpaidDocumentsFiltered = this.unpaidDocuments.filter(customer => {
                    // Фильтр по коду клиента
                    if (codeFilter && !customer.customerCode.toLowerCase().includes(codeFilter)) {
                        return false;
                    }

                    // Фильтр по имени клиента
                    if (nameFilter && !customer.customerName.toLowerCase().includes(nameFilter)) {
                        return false;
                    }

                    // Фильтр по минимальной сумме долга
                    if (minDebt > 0 && customer.totalDebt < minDebt) {
                        return false;
                    }

                    return true;
                });
            },

            exportUnpaidToExcel() {
                if (!this.selectedArea) return;
                
                // Используем отфильтрованные данные для экспорта
                const dataToExport = this.unpaidDocumentsFiltered.length > 0 
                    ? this.unpaidDocumentsFiltered 
                    : this.unpaidDocuments;
                
                if (dataToExport.length === 0) {
                    alert('Нет данных для экспорта');
                    return;
                }
                
                // Подготовить данные для Excel - разворачиваем документы
                const excelData = [];
                dataToExport.forEach(customer => {
                    customer.documents.forEach(doc => {
                        excelData.push({
                            'Код клиента': customer.customerCode,
                            'Имя клиента': customer.customerName,
                            '№ Документа': doc.docNumber,
                            'Дата': doc.docDate,
                            'Сумма документа': doc.docSum,
                            'Оплачено': doc.paidAmount,
                            'Не оплачено': doc.unpaidAmount
                        });
                    });
                });
                
                // Создать рабочую книгу
                const wb = XLSX.utils.book_new();
                const ws = XLSX.utils.json_to_sheet(excelData);
                
                // Установить ширину колонок
                ws['!cols'] = [
                    { wch: 15 },  // Код клиента
                    { wch: 40 },  // Имя клиента
                    { wch: 15 },  // № Документа
                    { wch: 12 },  // Дата
                    { wch: 18 },  // Сумма документа
                    { wch: 18 },  // Оплачено
                    { wch: 18 }   // Не оплачено
                ];
                
                // Добавить лист в книгу
                XLSX.utils.book_append_sheet(wb, ws, 'Неоплаченные документы');
                
                // Сформировать имя файла
                const fileName = `Unpaid_${this.selectedArea.code}_${this.unpaidDateFrom}_${this.unpaidDateTo}${this.unpaidDocumentsFiltered.length !== this.unpaidDocuments.length ? '_filtered' : ''}.xlsx`;
                
                // Сохранить файл
                XLSX.writeFile(wb, fileName);
            },
            
            calculatePercent(value, total) {
                if (!value || !total || total === 0) return 0;
                return Math.min(100, Math.round((value / total) * 100));
            },
            
            getEfficiencyPercent(value, total) {
                if (!value || !total || total === 0) return '0%';
                return ((value / total) * 100).toFixed(1) + '%';
            },
            
            getEfficiencyClass(value, total) {
                if (!total || total === 0) return 'bg-secondary';
                const percent = value / total;
                if (percent >= 0.8) return 'bg-success';
                if (percent >= 0.5) return 'bg-warning text-dark';
                return 'bg-danger';
            },

            // Helper functions for new metrics
            calculateRatio(numerator, denominator) {
                if (denominator === 0) return 'N/A';
                const ratio = (numerator / denominator * 100);
                return `${ratio.toFixed(1)}%`;
            },

            getDebtRatioClass(area) {
                if (area.TotalSales === 0) return 'bg-secondary';
                const ratio = area.Debt / area.TotalSales;
                if (ratio > 0.8) return 'bg-danger';
                if (ratio > 0.5) return 'bg-warning text-dark';
                return 'bg-success';
            },

            getPaymentRatioClass(area) {
                if (area.TotalSales === 0) return 'bg-secondary';
                const ratio = area.Payments / area.TotalSales;
                if (ratio < 0.5) return 'bg-danger';
                if (ratio < 0.8) return 'bg-warning text-dark';
                return 'bg-success';
            },
            
            getDebtRatioProgressClass(area) {
                if (area.TotalSales === 0) return 'bg-secondary';
                const ratio = area.Debt / area.TotalSales;
                if (ratio > 0.8) return 'bg-danger';
                if (ratio > 0.5) return 'bg-warning';
                return 'bg-success';
            },
            
            getPaymentRatioProgressClass(area) {
                if (area.TotalSales === 0) return 'bg-secondary';
                const ratio = area.Payments / area.TotalSales;
                if (ratio < 0.5) return 'bg-danger';
                if (ratio < 0.8) return 'bg-warning';
                return 'bg-success';
            },
            
            getDebtRatioStatus(area) {
                if (area.TotalSales === 0) return 'Нет данных';
                const ratio = area.Debt / area.TotalSales;
                if (ratio > 0.8) return 'Критический уровень';
                if (ratio > 0.5) return 'Требует внимания';
                return 'Нормальный уровень';
            },
            
            getPaymentRatioStatus(area) {
                if (area.TotalSales === 0) return 'Нет данных';
                const ratio = area.Payments / area.TotalSales;
                if (ratio < 0.5) return 'Низкое покрытие';
                if (ratio < 0.8) return 'Среднее покрытие';
                return 'Хорошее покрытие';
            },
            
            calculateCollectionEfficiency(area) {
                const total = area.InitialDebt + area.TotalSales;
                if (total === 0) return '0%';
                return ((area.Payments / total * 100).toFixed(1)) + '%';
            },
            
            getCollectionEfficiencyClass(area) {
                const total = area.InitialDebt + area.TotalSales;
                if (total === 0) return 'bg-secondary';
                const efficiency = area.Payments / total;
                if (efficiency >= 0.8) return 'bg-success';
                if (efficiency >= 0.6) return 'bg-warning text-dark';
                return 'bg-danger';
            },
            
            getCollectionEfficiencyProgressClass(area) {
                const total = area.InitialDebt + area.TotalSales;
                if (total === 0) return 'bg-secondary';
                const efficiency = area.Payments / total;
                if (efficiency >= 0.8) return 'bg-success';
                if (efficiency >= 0.6) return 'bg-warning';
                return 'bg-danger';
            },
            
            calculateDaysToCollect(area) {
                // Количество дней в периоде
                const dateFrom = new Date(this.dateFrom);
                const dateTo = new Date(this.dateTo);
                const days = Math.ceil((dateTo - dateFrom) / (1000 * 60 * 60 * 24)) + 1;
                
                if (area.TotalSales === 0 || days === 0) return 'N/A';
                
                const salesPerDay = area.TotalSales / days;
                const avgDebt = (area.InitialDebt + area.Debt) / 2;
                
                if (salesPerDay === 0) return 'N/A';
                
                const daysToCollect = Math.round(avgDebt / salesPerDay);
                return `${daysToCollect} дн.`;
            },
            
            getPaymentDisciplinePercentage(area) {
                if (area.TotalSales === 0) return '0%';
                const ratio = area.Payments / area.TotalSales;
                return (ratio * 100).toFixed(1) + '%';
            },
            
            getPaymentDisciplineValue(area) {
                if (area.TotalSales === 0) return 0;
                return Math.min((area.Payments / area.TotalSales * 100), 100);
            },
            
            getFinancialRecommendations(area) {
                const recommendations = [];
                
                // Анализ долговой нагрузки
                if (area.TotalSales > 0) {
                    const debtRatio = area.Debt / area.TotalSales;
                    if (debtRatio > 0.8) {
                        recommendations.push('<div class="alert alert-danger py-2 mb-2"><i class="fas fa-exclamation-triangle me-2"></i><strong>Критично:</strong> Долговая нагрузка превышает 80%. Срочно усилить работу по взысканию.</div>');
                    } else if (debtRatio > 0.5) {
                        recommendations.push('<div class="alert alert-warning py-2 mb-2"><i class="fas fa-exclamation-circle me-2"></i><strong>Внимание:</strong> Долговая нагрузка выше 50%. Рекомендуется активизировать работу с дебиторами.</div>');
                    }
                    
                    // Анализ покрытия оплат
                    const paymentRatio = area.Payments / area.TotalSales;
                    if (paymentRatio < 0.5) {
                        recommendations.push('<div class="alert alert-danger py-2 mb-2"><i class="fas fa-money-bill-wave me-2"></i><strong>Критично:</strong> Низкое покрытие оплат (менее 50%). Необходим контроль кредитных лимитов.</div>');
                    } else if (paymentRatio < 0.8) {
                        recommendations.push('<div class="alert alert-warning py-2 mb-2"><i class="fas fa-hand-holding-usd me-2"></i><strong>Рекомендация:</strong> Увеличить работу по сбору оплат для достижения целевого уровня 80%+.</div>');
                    }
                    
                    // Анализ роста долга
                    if (area.Debt > area.InitialDebt * 1.2) {
                        recommendations.push('<div class="alert alert-danger py-2 mb-2"><i class="fas fa-chart-line me-2"></i><strong>Тревога:</strong> Долг вырос более чем на 20%. Требуется срочный анализ причин и план действий.</div>');
                    }
                }
                
                // Сравнение с прошлыми периодами
                if (area.PrevMonthSales && area.TotalSales < area.PrevMonthSales * 0.9) {
                    recommendations.push('<div class="alert alert-warning py-2 mb-2"><i class="fas fa-arrow-down me-2"></i><strong>Внимание:</strong> Продажи снизились более чем на 10% по сравнению с прошлым месяцем.</div>');
                }
                
                if (recommendations.length === 0) {
                    return '<div class="alert alert-success py-2 mb-0"><i class="fas fa-check-circle me-2"></i><strong>Отлично:</strong> Финансовое состояние территории стабильное. Продолжайте поддерживать текущий уровень работы.</div>';
                }
                
                return recommendations.join('');
            },

            getChangeClass(current, previous) {
                if (previous === 0) return 'bg-info';
                return current > previous ? 'bg-success' : 'bg-danger';
            },

            getChangePercent(current, previous) {
                if (previous === 0) return 'N/A';
                const change = ((current - previous) / previous * 100);
                const sign = change > 0 ? '+' : '';
                return `${sign}${change.toFixed(1)}%`;
            },
            
            getHistoricalData(monthsBack, metric) {
                if (!this.selectedArea || !this.selectedArea.MonthlyHistory) return 0;
                const history = this.selectedArea.MonthlyHistory;
                if (history.length === 0) return 0;
                
                // Получить данные за указанное количество месяцев назад от последнего месяца в истории
                const index = history.length - 1 - monthsBack;
                if (index < 0 || index >= history.length) return 0;
                
                return history[index][metric] || 0;
            },
            
            getHistoricalMonth(monthsBack) {
                if (!this.selectedArea || !this.selectedArea.MonthlyHistory) return '';
                const history = this.selectedArea.MonthlyHistory;
                if (history.length === 0) return '';
                
                const index = history.length - 1 - monthsBack;
                if (index < 0 || index >= history.length) return '';
                
                return history[index].monthName || '';
            },
            
            getLastYearCustomers() {
                return this.getHistoricalData(12, 'customerCount');
            },
            
            getPrevMonthCustomers() {
                return this.getHistoricalData(1, 'customerCount');
            },
            
            getLastYearSalesCount() {
                return this.getHistoricalData(12, 'salesCount');
            },
            
            getPrevMonthSalesCount() {
                return this.getHistoricalData(1, 'salesCount');
            },

            renderAreaComparisonChart() {
                this.$nextTick(() => {
                    const ctx = document.getElementById('areaSalesComparisonChart');
                    if (!ctx || !this.selectedArea) return;

                    if (this.areaChart) {
                        this.areaChart.destroy();
                    }

                    const labels = ['Прошлый год', 'Прошлый месяц', 'Текущий период'];
                    const data = [
                        this.selectedArea.LastYearSales,
                        this.selectedArea.PrevMonthSales,
                        this.selectedArea.TotalSales
                    ];

                    this.areaChart = new Chart(ctx, {
                        type: 'bar',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: 'Сумма продаж',
                                data: data,
                                backgroundColor: [
                                    'rgba(108, 117, 125, 0.6)',
                                    'rgba(25, 135, 84, 0.6)',
                                    'rgba(13, 110, 253, 0.6)'
                                ],
                                borderColor: [
                                    'rgba(108, 117, 125, 1)',
                                    'rgba(25, 135, 84, 1)',
                                    'rgba(13, 110, 253, 1)'
                                ],
                                borderWidth: 1
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: {
                                legend: { display: false },
                                tooltip: {
                                    callbacks: {
                                        label: (context) => `Сумма: ${this.formatCurrency(context.raw)}`
                                    }
                                }
                            },
                            scales: {
                                y: {
                                    beginAtZero: true,
                                    ticks: {
                                        callback: (value) => this.formatCurrency(value)
                                    }
                                }
                            }
                        }
                    });
                });
            },

            renderMonthlyHistoryCharts() {
                if (!this.selectedArea || !this.selectedArea.MonthlyHistory) {
                    console.warn('No selected area or monthly history data');
                    return;
                }
                
                const history = this.selectedArea.MonthlyHistory;
                if (history.length === 0) {
                    console.warn('Monthly history is empty');
                    return;
                }

                this.$nextTick(() => {
                    const labels = history.map(h => h.monthName);
                    const salesData = history.map(h => h.totalSales);
                    const paymentsData = history.map(h => h.totalPayments || 0);
                    const debtData = history.map(h => h.totalDebt || 0);
                    const customersData = history.map(h => h.customerCount);
                    
                    console.log('Rendering monthly history charts with', history.length, 'data points');

                    // График продаж и оплат
                    const salesPaymentsCtx = document.getElementById('areaSalesPaymentsChart');
                    if (salesPaymentsCtx) {
                        if (this.salesPaymentsChart) {
                            this.salesPaymentsChart.destroy();
                        }

                        this.salesPaymentsChart = new Chart(salesPaymentsCtx, {
                            type: 'line',
                            data: {
                                labels: labels,
                                datasets: [
                                    {
                                        label: 'Продажи',
                                        data: salesData,
                                        borderColor: 'rgba(13, 110, 253, 1)',
                                        backgroundColor: 'rgba(13, 110, 253, 0.1)',
                                        borderWidth: 2,
                                        tension: 0.4,
                                        fill: true
                                    },
                                    {
                                        label: 'Оплаты',
                                        data: paymentsData,
                                        borderColor: 'rgba(25, 135, 84, 1)',
                                        backgroundColor: 'rgba(25, 135, 84, 0.1)',
                                        borderWidth: 2,
                                        borderDash: [6, 4],
                                        tension: 0.4,
                                        fill: true
                                    }
                                ]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: { 
                                        display: true,
                                        position: 'top'
                                    },
                                    tooltip: {
                                        callbacks: {
                                            label: (context) => `${context.dataset.label}: ${this.formatCurrency(context.raw)}`
                                        }
                                    }
                                },
                                scales: {
                                    y: {
                                        beginAtZero: true,
                                        ticks: {
                                            callback: (value) => this.formatCurrency(value)
                                        }
                                    },
                                    x: {
                                        ticks: {
                                            maxRotation: 45,
                                            minRotation: 45
                                        }
                                    }
                                }
                            }
                        });
                    }

                    // Отдельный график долга
                    const debtCtx = document.getElementById('areaDebtChart');
                    if (debtCtx) {
                        if (this.debtChart) {
                            this.debtChart.destroy();
                        }

                        this.debtChart = new Chart(debtCtx, {
                            type: 'line',
                            data: {
                                labels: labels,
                                datasets: [
                                    {
                                        label: 'Долг',
                                        data: debtData,
                                        borderColor: 'rgba(220, 53, 69, 1)',
                                        backgroundColor: 'rgba(220, 53, 69, 0.1)',
                                        borderWidth: 3,
                                        tension: 0.4,
                                        fill: true
                                    }
                                ]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: { 
                                        display: true,
                                        position: 'top'
                                    },
                                    tooltip: {
                                        callbacks: {
                                            label: (context) => `Долг: ${this.formatCurrency(context.raw)}`
                                        }
                                    }
                                },
                                scales: {
                                    y: {
                                        beginAtZero: true,
                                        ticks: {
                                            callback: (value) => this.formatCurrency(value)
                                        }
                                    },
                                    x: {
                                        ticks: {
                                            maxRotation: 45,
                                            minRotation: 45
                                        }
                                    }
                                }
                            }
                        });
                    }

                    // График клиентов по месяцам
                    const customersCtx = document.getElementById('areaCustomersHistoryChart');
                    if (customersCtx) {
                        if (this.customersHistoryChart) {
                            this.customersHistoryChart.destroy();
                        }

                        this.customersHistoryChart = new Chart(customersCtx, {
                            type: 'bar',
                            data: {
                                labels: labels,
                                datasets: [{
                                    label: 'Количество клиентов',
                                    data: customersData,
                                    backgroundColor: 'rgba(25, 135, 84, 0.7)',
                                    borderColor: 'rgba(25, 135, 84, 1)',
                                    borderWidth: 1
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: { display: false },
                                    tooltip: {
                                        callbacks: {
                                            label: (context) => `Клиентов: ${this.formatNumber(context.raw)}`
                                        }
                                    }
                                },
                                scales: {
                                    y: {
                                        beginAtZero: true,
                                        ticks: {
                                            callback: (value) => this.formatNumber(value)
                                        }
                                    },
                                    x: {
                                        ticks: {
                                            maxRotation: 45,
                                            minRotation: 45
                                        }
                                    }
                                }
                            }
                        });
                    }
                });
            },

            renderChart() {
                const ctx = document.getElementById('areasChart');
                if (!ctx) return;

                if (this.chart) {
                    this.chart.destroy();
                }

                const labels = this.areas.map(a => a.name || a.code || 'N/A');
                const salesData = this.areas.map(a => a.SalesCount);
                const revenueData = this.areas.map(a => a.TotalSales);

                this.chart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Количество продаж',
                                data: salesData,
                                backgroundColor: 'rgba(13, 110, 253, 0.6)',
                                borderColor: 'rgba(13, 110, 253, 1)',
                                borderWidth: 1,
                                yAxisID: 'y'
                            },
                            {
                                label: 'Выручка (AMD)',
                                data: revenueData,
                                type: 'line',
                                borderColor: 'rgba(25, 135, 84, 1)',
                                backgroundColor: 'rgba(25, 135, 84, 0.1)',
                                borderWidth: 2,
                                yAxisID: 'y1',
                                tension: 0.4
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
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        let label = context.dataset.label || '';
                                        if (label) {
                                            label += ': ';
                                        }
                                        if (context.dataset.yAxisID === 'y1') {
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
                            y: {
                                type: 'linear',
                                display: true,
                                position: 'left',
                                title: {
                                    display: true,
                                    text: 'Количество продаж'
                                },
                                ticks: {
                                    callback: function(value) {
                                        return formatNumber(value);
                                    }
                                }
                            },
                            y1: {
                                type: 'linear',
                                display: true,
                                position: 'right',
                                title: {
                                    display: true,
                                    text: 'Выручка (AMD)'
                                },
                                grid: {
                                    drawOnChartArea: false
                                },
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

            getWorkingDaysInMonth() {
                const now = new Date();
                const year = now.getFullYear();
                const month = now.getMonth();
                const daysInMonth = new Date(year, month + 1, 0).getDate();
                let workingDays = 0;
                for (let day = 1; day <= daysInMonth; day++) {
                    const date = new Date(year, month, day);
                    if (date.getDay() !== 0) workingDays++;
                }
                return workingDays;
            },

            getWorkingDaysPassed() {
                const now = new Date();
                const year = now.getFullYear();
                const month = now.getMonth();
                const currentDay = now.getDate();
                let workingDays = 0;
                for (let day = 1; day <= currentDay; day++) {
                    const date = new Date(year, month, day);
                    if (date.getDay() !== 0) workingDays++;
                }
                return workingDays;
            }
        }
    }

    // Инициализация drag & drop для карточек территорий
    document.addEventListener('DOMContentLoaded', () => {
        const container = document.getElementById('areas-container');
        if (container) {
            new Sortable(container, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                handle: '.card-header',
                onEnd: function() {
                    console.log('Порядок территорий изменён');
                }
            });
        }
        
        // Инициализация обработчика для вкладки динамики
        const modal = document.getElementById('areaDetailsModal');
        if (modal) {
            modal.addEventListener('shown.bs.modal', () => {
                console.log('Modal shown');
                // Добавить обработчик для переключения вкладок
                const dynamicsTab = document.querySelector('#dynamics-tab');
                if (dynamicsTab) {
                    dynamicsTab.addEventListener('shown.bs.tab', function(e) {
                        console.log('Dynamics tab activated');
                        // Получить Alpine компонент
                        const alpineComponent = Alpine.$data(document.querySelector('[x-data]'));
                        if (alpineComponent && alpineComponent.selectedArea) {
                            setTimeout(() => {
                                alpineComponent.renderAreaComparisonChart();
                                alpineComponent.renderMonthlyHistoryCharts();
                            }, 150);
                        }
                    }, { once: false });
                }
            });
        }
    });

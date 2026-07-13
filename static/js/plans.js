/* plans — page script (extracted from inline <script>). */
    // Отключаем плагин DataLabels глобально (если он загружен)
    if (Chart.defaults.plugins && Chart.defaults.plugins.datalabels) {
        Chart.defaults.plugins.datalabels.display = false;
    }

    function plansData() {
        // Store charts outside of Alpine reactive scope to prevent stack overflow
        let areaCharts = {};
        let seasonalityChart = null;

        return {
            areas: [],
            loading: true,
            selectedMonth: new Date().getMonth() + 1,
            selectedYear: new Date().getFullYear(),
            plans: {},
            areaGrowth: {},
            areaSeasonality: {},
            showSaveIndicator: false,
            showSettings: false,
            seasonalityCoeff: null,
            areaHistory: {},
            showFiltersPanel: false,
            groups: [],
            selectedGroups: [],
            groupSearch: '',
            selectedDebtGroups: [],  // Separate groups filter for debt calculations
            debtGroupSearch: '',
            // seasonalityChart and areaCharts removed from here
            areaCalculatedSeasonality: {}, // Store calculated profiles from API
            seasonalityMap: {
                1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
                5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
                9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
            },
            settings: {
                growthFactor: 10,
                historyYears: 2,
                seasonality: {
                    1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
                    5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
                    9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
                }
            },
            defaultSettings: {
                growthFactor: 10,
                historyYears: 2,
                seasonality: {
                    1: 0.53, 2: 0.67, 3: 0.80, 4: 0.86,
                    5: 1.14, 6: 1.31, 7: 1.49, 8: 1.43,
                    9: 1.10, 10: 1.02, 11: 0.88, 12: 0.93
                }
            },

            async init() {
                this.loading = true;
                this.loadSettings();
                await this.loadGroups();
                this.loadGroupsFromStorage();
                await this.loadAreas();
                this.updateSeasonalityInfo();
                await this.fetchCalculatedSeasonality();
                this.loadPlans();
                this.loadPrevMonthHistory();
                this.createSeasonalityChart();
                this.loading = false;
            },

            async onPeriodChange() {
                this.loading = true;
                this.updateSeasonalityInfo();
                await this.fetchCalculatedSeasonality();
                this.loadPlans();
                this.loadPrevMonthHistory();
                this.loading = false;
            },

            async fetchCalculatedSeasonality() {
                try {
                    console.log('Fetching calculated seasonality...');
                    const params = new URLSearchParams();
                    if (this.selectedGroups.length > 0) {
                        params.append('groups', this.selectedGroups.join(','));
                    }

                    const response = await fetch(`/api/area-seasonality?${params}`);
                    const data = await response.json();

                    if (data.success) {
                        console.log('Received seasonality data:', data.data);
                        this.areaCalculatedSeasonality = data.data;

                        let updatedCount = 0;

                        this.areas.forEach(area => {
                            const areaCode = String(area.code).trim();

                            // Find profile (handle potential key mismatches)
                            let calculatedProfile = this.areaCalculatedSeasonality[areaCode];
                            if (!calculatedProfile) {
                                // Try finding key that matches loosely
                                const key = Object.keys(this.areaCalculatedSeasonality).find(k => String(k).trim() == areaCode);
                                if (key) calculatedProfile = this.areaCalculatedSeasonality[key];
                            }

                            if (calculatedProfile) {
                                // Handle month key (string vs number)
                                const monthKey = String(this.selectedMonth);
                                const val = calculatedProfile[monthKey] || calculatedProfile[parseInt(monthKey)];

                                if (val !== undefined) {
                                    // Direct assignment is safe here because charts are not rendered yet (loading=true)
                                    this.areaSeasonality[area.code] = Number(val);
                                    updatedCount++;
                                }
                            }
                        });

                        console.log(`Updated seasonality for ${updatedCount} areas`);

                        // Show success message
                        const toast = document.createElement('div');
                        toast.className = 'save-indicator show';
                        toast.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
                        toast.innerHTML = `<i class="fas fa-magic me-2"></i>Сезонность обновлена (${updatedCount} терр.)`;
                        document.body.appendChild(toast);
                        setTimeout(() => toast.remove(), 3000);
                    }
                } catch (error) {
                    console.error('Error fetching calculated seasonality:', error);
                }
            },

            initAreaChart(areaCode, canvas) {
                if (areaCharts[areaCode]) {
                    areaCharts[areaCode].destroy();
                }

                const ctx = canvas.getContext('2d');
                const labels = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];
                const data = [];

                // Use calculated profile if available, otherwise global settings
                let profile = this.areaCalculatedSeasonality[areaCode];
                if (!profile) {
                    const key = Object.keys(this.areaCalculatedSeasonality).find(k => String(k).trim() == String(areaCode).trim());
                    if (key) profile = this.areaCalculatedSeasonality[key];
                }
                if (!profile) profile = this.settings.seasonality;

                for (let i = 1; i <= 12; i++) {
                    if (i === parseInt(this.selectedMonth)) {
                        // For current month, use the input value if it differs significantly from profile
                        // But actually, the input value IS the value for this month.
                        // Let's show the input value for the selected month point
                        data.push(parseFloat(this.areaSeasonality[areaCode]) || profile[i]);
                    } else {
                        data.push(profile[i]);
                    }
                }

                const config = {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Сезонность',
                            data: data,
                            borderColor: 'rgba(255, 255, 255, 0.6)',
                            borderWidth: 2,
                            pointBackgroundColor: data.map((_, i) => i + 1 === parseInt(this.selectedMonth) ? '#ffc107' : 'rgba(255, 255, 255, 0.6)'),
                            pointRadius: data.map((_, i) => i + 1 === parseInt(this.selectedMonth) ? 4 : 2),
                            fill: false,
                            tension: 0.4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                enabled: true,
                                callbacks: {
                                    label: function (context) {
                                        return context.parsed.y.toFixed(2);
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                display: false
                            },
                            y: {
                                display: false,
                                min: 0.4,
                                max: 1.6
                            }
                        }
                    }
                };

                areaCharts[areaCode] = new Chart(ctx, config);
            },

            updateAreaChart(areaCode) {
                if (!areaCharts[areaCode]) return;

                const chart = areaCharts[areaCode];
                const newData = [];

                // Use calculated profile if available, otherwise global settings
                let profile = this.areaCalculatedSeasonality[areaCode];
                if (!profile) {
                    const key = Object.keys(this.areaCalculatedSeasonality).find(k => String(k).trim() == String(areaCode).trim());
                    if (key) profile = this.areaCalculatedSeasonality[key];
                }
                if (!profile) profile = this.settings.seasonality;

                for (let i = 1; i <= 12; i++) {
                    if (i === parseInt(this.selectedMonth)) {
                        newData.push(parseFloat(this.areaSeasonality[areaCode]) || profile[i]);
                    } else {
                        newData.push(profile[i]);
                    }
                }

                chart.data.datasets[0].data = newData;
                chart.update();
            },

            async loadGroups() {
                try {
                    const response = await fetch('/api/customer-groups-hierarchy');
                    const data = await response.json();
                    if (data.success) {
                        // The hierarchy endpoint returns { data: { flat: [...], tree: [...] } }
                        // We use the flat list which contains the 'parent' field needed for groupListByParent
                        this.groups = data.data.flat;
                    }
                } catch (error) {
                    console.error('Error loading groups:', error);
                }
            },

            loadGroupsFromStorage() {
                const savedSales = localStorage.getItem('plans_selected_groups');
                if (savedSales) {
                    try {
                        this.selectedGroups = JSON.parse(savedSales);
                    } catch (e) {
                        console.error('Error loading sales groups from storage:', e);
                    }
                }
                const savedDebt = localStorage.getItem('plans_selected_debt_groups');
                if (savedDebt) {
                    try {
                        this.selectedDebtGroups = JSON.parse(savedDebt);
                    } catch (e) {
                        console.error('Error loading debt groups from storage:', e);
                    }
                }
            },

            saveGroupsToStorage() {
                localStorage.setItem('plans_selected_groups', JSON.stringify(this.selectedGroups));
                localStorage.setItem('plans_selected_debt_groups', JSON.stringify(this.selectedDebtGroups));
            },

            selectAllGroups() {
                this.selectedGroups = this.groups.map(g => g.code);
                this.saveGroupsToStorage();
                this.loadPrevMonthHistory();
            },

            clearFilters() {
                this.selectedGroups = [];
                this.selectedDebtGroups = [];  // Clear both
                this.saveGroupsToStorage();
                this.loadPrevMonthHistory();
            },

            // Toggle group selection with hierarchical expansion
            // When selecting a parent group, also select all its children
            toggleGroupWithChildren(groupCode) {
                const isSelected = this.selectedGroups.includes(groupCode);

                // Find all children of this group
                const children = this.groups.filter(g => g.parent === groupCode).map(g => g.code);
                const allCodes = [groupCode, ...children];

                console.log(`[toggleGroup] ${groupCode}, isSelected: ${isSelected}, children: ${children.length}`);

                if (isSelected) {
                    // Deselect this group and all children
                    this.selectedGroups = this.selectedGroups.filter(code => !allCodes.includes(code));
                } else {
                    // Select this group and all children
                    allCodes.forEach(code => {
                        if (!this.selectedGroups.includes(code)) {
                            this.selectedGroups.push(code);
                        }
                    });
                }

                this.saveGroupsToStorage();
                this.loadPrevMonthHistory();
            },

            filteredGroups() {
                if (!this.groupSearch) return this.groups;
                const search = this.groupSearch.toLowerCase();
                return this.groups.filter(g =>
                    g.code.toLowerCase().includes(search) ||
                    g.name.toLowerCase().includes(search)
                );
            },

            // Debt groups - separate filter for credit/debt calculations
            filteredDebtGroups() {
                if (!this.debtGroupSearch) return this.groups;
                const search = this.debtGroupSearch.toLowerCase();
                return this.groups.filter(g =>
                    g.code.toLowerCase().includes(search) ||
                    g.name.toLowerCase().includes(search)
                );
            },

            selectAllDebtGroups() {
                this.selectedDebtGroups = this.groups.map(g => g.code);
                this.saveGroupsToStorage();
                this.loadPrevMonthHistory();
            },

            toggleDebtGroupWithChildren(groupCode) {
                const isSelected = this.selectedDebtGroups.includes(groupCode);

                // Find all children of this group
                const children = this.groups.filter(g => g.parent === groupCode).map(g => g.code);
                const allCodes = [groupCode, ...children];

                console.log(`[toggleDebtGroup] ${groupCode}, isSelected: ${isSelected}, children: ${children.length}`);

                if (isSelected) {
                    this.selectedDebtGroups = this.selectedDebtGroups.filter(code => !allCodes.includes(code));
                } else {
                    allCodes.forEach(code => {
                        if (!this.selectedDebtGroups.includes(code)) {
                            this.selectedDebtGroups.push(code);
                        }
                    });
                }

                this.saveGroupsToStorage();
                this.loadPrevMonthHistory();
            },

            groupListByParent(groups) {
                // Create a map of code -> name for easy lookup
                const groupNames = {};
                groups.forEach(g => {
                    groupNames[g.code] = g.name;
                });

                const byParent = {};
                groups.forEach(g => {
                    const parentCode = g.parent;
                    let parentLabel = 'Без категории';

                    if (parentCode) {
                        // Try to find parent name
                        const parentName = groupNames[parentCode];
                        parentLabel = parentName ? `${parentCode} · ${parentName}` : parentCode;
                    }

                    if (!byParent[parentLabel]) {
                        byParent[parentLabel] = [];
                    }
                    byParent[parentLabel].push(g);
                });

                // Sort keys to ensure consistent order (optional but nice)
                return Object.keys(byParent).sort().map(parent => ({
                    parent,
                    items: byParent[parent]
                }));
            },

            async loadAreas() {
                try {
                    this.loading = true;
                    const response = await fetch('/api/sales-areas');
                    const data = await response.json();

                    if (data.success) {
                        this.areas = data.data;
                        // Initialize areaGrowth with default global setting
                        this.areas.forEach(area => {
                            if (this.areaGrowth[area.code] === undefined) {
                                this.areaGrowth[area.code] = this.settings.growthFactor;
                            }
                            if (this.areaSeasonality[area.code] === undefined) {
                                this.areaSeasonality[area.code] = this.settings.seasonality[this.selectedMonth];
                            }
                        });
                    }
                } catch (error) {
                    console.error('Error loading areas:', error);
                }
            },

            async loadPlans() {
                const key = `plans_${this.selectedYear}_${this.selectedMonth}`;
                const saved = localStorage.getItem(key);
                console.log(`[loadPlans] Key: ${key}, Has saved data: ${!!saved}`);

                if (saved) {
                    this.plans = JSON.parse(saved);
                    console.log(`[loadPlans] Loaded ${Object.keys(this.plans).length} plans from localStorage`);
                } else {
                    // Auto-fetch calculated plans from backend on first load
                    console.log(`[loadPlans] No saved data, fetching from API...`);
                    try {
                        const params = new URLSearchParams({
                            month: this.selectedMonth,
                            year: this.selectedYear,
                            growth: this.settings.growthFactor
                        });
                        if (this.selectedGroups.length > 0) {
                            params.append('groups', this.selectedGroups.join(','));
                            console.log(`[loadPlans] Groups filter: ${this.selectedGroups.join(',')}`);
                        }

                        const url = `/api/generate-plans?${params}`;
                        console.log(`[loadPlans] Fetching: ${url}`);
                        const response = await fetch(url);
                        const data = await response.json();
                        console.log(`[loadPlans] API response success: ${data.success}, areas: ${Object.keys(data.data || {}).length}`);

                        if (data.success && data.data) {
                            this.plans = {};
                            for (const [areaCode, planData] of Object.entries(data.data)) {
                                // Store avg data in history for display
                                if (!this.areaHistory[areaCode]) {
                                    this.areaHistory[areaCode] = {};
                                }
                                this.areaHistory[areaCode].avgSales = planData.avg_sales || 0;
                                this.areaHistory[areaCode].avgCredit = planData.avg_credit || 0;

                                // Store plan values
                                this.plans[`${areaCode}_sales`] = planData.sales;
                                this.plans[`${areaCode}_credit`] = planData.credit;
                            }
                            console.log(`[loadPlans] Stored plans for ${Object.keys(data.data).length} areas`);
                            console.log(`[loadPlans] Sample: 101_sales = ${this.plans['101_sales']}`);
                        }
                    } catch (error) {
                        console.error('[loadPlans] Error:', error);
                        this.plans = {};
                    }
                }

                this.loadPrevMonthHistory();
            },

            getAreaPlan(areaCode, type) {
                const key = `${areaCode}_${type}`;
                const value = this.plans[key] || '';
                // Uncomment for verbose logging:
                // console.log(`[getAreaPlan] ${key} = ${value}`);
                return value;
            },

            updateAreaPlan(areaCode, type, value) {
                const key = `${areaCode}_${type}`;
                this.plans[key] = value;
            },

            formatPlanInput(value) {
                if (!value) return '';
                const num = typeof value === 'string' ? parseInt(value.replace(/\s/g, '')) : value;
                if (isNaN(num)) return '';
                return num.toLocaleString('ru-RU');
            },

            parsePlanInput(value) {
                if (!value) return '';
                const cleaned = value.replace(/\s/g, '');
                const num = parseInt(cleaned);
                return isNaN(num) ? '' : num;
            },

            updateAreaPlanFormatted(areaCode, type, value) {
                const key = `${areaCode}_${type}`;
                const parsed = this.parsePlanInput(value);
                this.plans[key] = parsed;
            },

            saveAreaPlan(areaCode) {
                this.savePlans();
                this.showSaveNotification();
            },

            saveAllPlans() {
                this.savePlans();
                this.showSaveNotification();
            },

            async generatePlans() {
                console.log('[generatePlans] Starting...');
                const growthPercent = this.settings.growthFactor;
                const seasonCoeff = parseFloat(this.settings.seasonality[this.selectedMonth]) || 1.0;
                const yearsText = this.settings.historyYears == 1 ? '1 год' : (this.settings.historyYears < 5 ? `${this.settings.historyYears} года` : `${this.settings.historyYears} лет`);

                console.log(`[generatePlans] Groups selected: ${this.selectedGroups.length}`, this.selectedGroups);

                if (!confirm(`Сгенерировать автоматические планы для ${this.getMonthName(this.selectedMonth)} ${this.selectedYear}?\n\nБудут учтены:\n- Средние продажи за последние 12 месяцев\n- Средние кредиты за последние 12 месяцев\n- История для сезонности: ${yearsText}\n- Сезонность: ${seasonCoeff.toFixed(2)}\n- Рост: ${growthPercent > 0 ? '+' : ''}${growthPercent}%${this.selectedGroups.length > 0 ? '\n- Группы клиентов: ' + this.selectedGroups.length + ' выбрано' : ''}`)) {
                    console.log('[generatePlans] User cancelled');
                    return;
                }

                try {
                    this.loading = true;
                    console.log('[generatePlans] Sending POST request...');

                    const payload = {
                        month: this.selectedMonth,
                        year: this.selectedYear,
                        history_years: this.settings.historyYears,
                        growth: this.settings.growthFactor,
                        growth_map: this.areaGrowth,
                        seasonality_map: this.areaSeasonality,
                        groups: this.selectedGroups,       // For sales filtering
                        debt_groups: this.selectedDebtGroups  // For debt filtering
                    };
                    console.log('[generatePlans] Payload:', JSON.stringify(payload));

                    const response = await fetch('/api/generate-plans', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(payload)
                    });
                    const data = await response.json();
                    console.log('[generatePlans] Response:', data.success, 'Areas:', Object.keys(data.data || {}).length);

                    if (data.success) {
                        // Применяем сгенерированные планы с расчетами из API
                        for (const [areaCode, planData] of Object.entries(data.data)) {
                            // Обновляем историю, чтобы отображаемые средние значения совпадали с расчетом
                            if (!this.areaHistory[areaCode]) {
                                this.areaHistory[areaCode] = {};
                            }
                            this.areaHistory[areaCode].avgSales = planData.avg_sales || 0;
                            this.areaHistory[areaCode].avgCredit = planData.avg_credit || 0;

                            // Store calculated seasonality profile if available
                            if (planData.calculated_seasonality) {
                                this.areaCalculatedSeasonality[areaCode] = planData.calculated_seasonality;
                                // Update chart if it exists
                                this.updateAreaChart(areaCode);
                            }

                            // Используем уже округленные значения из API (округлено до 10,000)
                            this.plans[`${areaCode}_sales`] = planData.sales;
                            this.plans[`${areaCode}_credit`] = planData.credit;
                        }

                        this.savePlans();
                        await this.loadPrevMonthHistory();  // Refresh historical data with current groups
                        this.showSaveNotification();

                        const yearsText = this.settings.historyYears == 1 ? '1 год' : (this.settings.historyYears < 5 ? `${this.settings.historyYears} года` : `${this.settings.historyYears} лет`);
                        alert(`Планы успешно сгенерированы!\n\nПараметры:\n- История: ${yearsText}\n- Сезонность: ${seasonCoeff.toFixed(2)}\n- Рост: ${growthPercent > 0 ? '+' : ''}${growthPercent}%\n- Кредиты рассчитаны по реальной истории`);
                    } else {
                        alert('Ошибка генерации планов: ' + data.error);
                    }
                } catch (error) {
                    console.error('Error generating plans:', error);
                    alert('Ошибка генерации планов');
                } finally {
                    this.loading = false;
                }
            },

            savePlans() {
                const key = `plans_${this.selectedYear}_${this.selectedMonth}`;
                localStorage.setItem(key, JSON.stringify(this.plans));
            },

            resetAllPlans() {
                if (confirm('Вы уверены, что хотите сбросить все планы для этого месяца?')) {
                    this.plans = {};
                    this.savePlans();
                    this.showSaveNotification();
                }
            },

            showSaveNotification() {
                this.showSaveIndicator = true;
                setTimeout(() => {
                    this.showSaveIndicator = false;
                }, 2000);
            },

            formatCurrency(value) {
                if (!value) return '0';
                return parseFloat(value).toLocaleString('ru-RU');
            },

            getTotalSales() {
                let total = 0;
                this.areas.forEach(area => {
                    const plan = this.getAreaPlan(area.code, 'sales');
                    total += parseFloat(plan) || 0;
                });
                return Math.round(total);
            },

            getTotalCredit() {
                let total = 0;
                this.areas.forEach(area => {
                    const plan = this.getAreaPlan(area.code, 'credit');
                    total += parseFloat(plan) || 0;
                });
                return Math.round(total);
            },

            getTotalCustomers() {
                let total = 0;
                this.areas.forEach(area => {
                    total += parseInt(area.CustomerCount) || 0;
                });
                return total;
            },

            getTotalManagers() {
                let total = 0;
                this.areas.forEach(area => {
                    total += parseInt(area.ManagerCount) || 0;
                });
                return total;
            },

            getMonthName(month) {
                const names = {
                    1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
                    5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
                    9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
                };
                return names[month] || '';
            },

            updateSeasonalityInfo() {
                const value = this.settings.seasonality[this.selectedMonth];
                this.seasonalityCoeff = value ? parseFloat(value) : null;

                // Update area seasonality defaults when month changes
                if (this.areas) {
                    this.areas.forEach(area => {
                        this.areaSeasonality[area.code] = this.seasonalityCoeff;
                    });
                }
            },

            exportToExcel() {
                // Собираем данные для экспорта
                const exportData = [];

                this.areas.forEach(area => {
                    const salesPlan = this.getAreaPlan(area.code, 'sales') || 0;
                    const creditPlan = this.getAreaPlan(area.code, 'credit') || 0;

                    // Добавляем только те территории, у которых есть планы
                    if (salesPlan > 0 || creditPlan > 0) {
                        const history = this.areaHistory[area.code] || {};
                        const avgSales = Math.round(history.avgSales || 0);
                        const avgCredit = Math.round(history.avgCredit || 0);

                        // Рассчитываем отклонения
                        const salesDiff = salesPlan - avgSales;
                        const creditDiff = creditPlan - avgCredit;
                        const salesGrowth = avgSales > 0 ? ((salesDiff / avgSales) * 100).toFixed(1) : 0;
                        const creditGrowth = avgCredit > 0 ? ((creditDiff / avgCredit) * 100).toFixed(1) : 0;

                        exportData.push({
                            'Код': area.code,
                            'Территория': area.name,
                            'План продаж (AMD)': salesPlan,
                            'План кредитов (AMD)': creditPlan,
                            'Средние продажи (AMD)': avgSales,
                            'Средний долг (AMD)': avgCredit,
                            'Рост продаж (%)': salesGrowth,
                            'Рост кредитов (%)': creditGrowth,
                            'Разница продаж (AMD)': salesDiff,
                            'Разница кредитов (AMD)': creditDiff
                        });
                    }
                });

                if (exportData.length === 0) {
                    alert('Нет данных для экспорта. Сгенерируйте или введите планы.');
                    return;
                }

                // Создаем HTML таблицу для Excel
                const monthName = this.getMonthName(this.selectedMonth);
                const title = `Планы продаж и кредитов - ${monthName} ${this.selectedYear}`;
                const date = new Date().toLocaleDateString('ru-RU');

                let html = `
                    <html xmlns:x="urn:schemas-microsoft-com:office:excel">
                    <head>
                        <meta charset="UTF-8">
                        <xml>
                            <x:ExcelWorkbook>
                                <x:ExcelWorksheets>
                                    <x:ExcelWorksheet>
                                        <x:Name>Планы</x:Name>
                                        <x:WorksheetOptions>
                                            <x:Print>
                                                <x:ValidPrinterInfo/>
                                            </x:Print>
                                        </x:WorksheetOptions>
                                    </x:ExcelWorksheet>
                                </x:ExcelWorksheets>
                            </x:ExcelWorkbook>
                        </xml>
                        <style>
                            table { 
                                border-collapse: collapse; 
                                font-family: Calibri, sans-serif; 
                                font-size: 11pt;
                                width: 100%;
                            }
                            .title { 
                                font-size: 16pt; 
                                font-weight: bold; 
                                color: #2c3e50;
                                padding: 15px 0 10px 0;
                                text-align: left;
                            }
                            .subtitle { 
                                font-size: 10pt; 
                                color: #7f8c8d;
                                padding-bottom: 20px;
                            }
                            th { 
                                background-color: #3498db; 
                                color: white; 
                                font-weight: bold; 
                                padding: 12px 8px;
                                border: 1px solid #2980b9;
                                text-align: center;
                                white-space: nowrap;
                            }
                            td { 
                                padding: 8px; 
                                border: 1px solid #bdc3c7;
                                text-align: right;
                            }
                            td:nth-child(1), td:nth-child(2) { 
                                text-align: left;
                            }
                            tr:nth-child(even) { 
                                background-color: #ecf0f1; 
                            }
                            tr:hover { 
                                background-color: #d5dbdb; 
                            }
                            .positive { 
                                color: #27ae60; 
                                font-weight: bold; 
                            }
                            .negative { 
                                color: #e74c3c; 
                                font-weight: bold; 
                            }
                            .summary { 
                                background-color: #34495e !important; 
                                color: white !important;
                                font-weight: bold;
                            }
                            .number { 
                                mso-number-format: "#,##0"; 
                            }
                            .percent { 
                                mso-number-format: "0.0%"; 
                            }
                        </style>
                    </head>
                    <body>
                        <div class="title">${title}</div>
                        <div class="subtitle">Создано: ${date} | Территорий: ${exportData.length}</div>
                        <table border="1">
                            <thead>
                                <tr>`;

                // Заголовки
                const headers = Object.keys(exportData[0]);
                headers.forEach(header => {
                    html += `<th>${header}</th>`;
                });
                html += `</tr></thead><tbody>`;

                // Данные
                let totalSalesPlan = 0, totalCreditPlan = 0, totalAvgSales = 0, totalAvgCredit = 0;

                exportData.forEach(row => {
                    html += '<tr>';
                    headers.forEach(header => {
                        let value = row[header];
                        let cssClass = 'number';

                        // Форматирование по типу данных
                        if (header.includes('Код')) {
                            html += `<td style="text-align:left">${value}</td>`;
                        } else if (header.includes('Территория')) {
                            html += `<td style="text-align:left">${value}</td>`;
                        } else if (header.includes('Рост')) {
                            const numValue = parseFloat(value);
                            const className = numValue >= 0 ? 'positive' : 'negative';
                            html += `<td class="${className} number">${value}%</td>`;
                        } else if (header.includes('Разница')) {
                            const numValue = parseFloat(value);
                            const className = numValue >= 0 ? 'positive' : 'negative';
                            html += `<td class="${className} number">${value.toLocaleString('ru-RU')}</td>`;
                        } else {
                            html += `<td class="number">${value.toLocaleString('ru-RU')}</td>`;
                        }
                    });
                    html += '</tr>';

                    // Суммируем итоги
                    totalSalesPlan += row['План продаж (AMD)'];
                    totalCreditPlan += row['План кредитов (AMD)'];
                    totalAvgSales += row['Средние продажи (AMD)'];
                    totalAvgCredit += row['Средний долг (AMD)'];
                });

                // Итоговая строка
                const totalSalesGrowth = totalAvgSales > 0 ? (((totalSalesPlan - totalAvgSales) / totalAvgSales) * 100).toFixed(1) : 0;
                const totalCreditGrowth = totalAvgCredit > 0 ? (((totalCreditPlan - totalAvgCredit) / totalAvgCredit) * 100).toFixed(1) : 0;

                html += `
                    <tr class="summary">
                        <td colspan="2" style="text-align:center">ИТОГО</td>
                        <td class="number">${totalSalesPlan.toLocaleString('ru-RU')}</td>
                        <td class="number">${totalCreditPlan.toLocaleString('ru-RU')}</td>
                        <td class="number">${totalAvgSales.toLocaleString('ru-RU')}</td>
                        <td class="number">${totalAvgCredit.toLocaleString('ru-RU')}</td>
                        <td class="number">${totalSalesGrowth}%</td>
                        <td class="number">${totalCreditGrowth}%</td>
                        <td class="number">${(totalSalesPlan - totalAvgSales).toLocaleString('ru-RU')}</td>
                        <td class="number">${(totalCreditPlan - totalAvgCredit).toLocaleString('ru-RU')}</td>
                    </tr>
                </tbody></table>
                    </body>
                    </html>`;

                // Скачиваем файл
                const blob = new Blob(['\ufeff', html], { type: 'application/vnd.ms-excel;charset=utf-8' });
                const link = document.createElement('a');
                link.href = URL.createObjectURL(blob);
                link.download = `Планы_${monthName}_${this.selectedYear}.xls`;
                link.click();

                // Показываем уведомление
                const notification = `
                    ✅ Экспортировано успешно!
                    
                    📊 Территорий: ${exportData.length}
                    💰 План продаж: ${(totalSalesPlan / 1000000).toFixed(1)} млн AMD
                    💳 План кредитов: ${(totalCreditPlan / 1000000).toFixed(1)} млн AMD
                `;
                alert(notification);
            },

            loadSettings() {
                const saved = localStorage.getItem('plan_generation_settings');
                if (saved) {
                    try {
                        this.settings = JSON.parse(saved);
                    } catch (e) {
                        console.error('Error loading settings:', e);
                    }
                }
            },

            saveSettings() {
                localStorage.setItem('plan_generation_settings', JSON.stringify(this.settings));
                this.updateSeasonalityInfo();
                this.createSeasonalityChart();
                this.showSettings = false;
                this.showSaveNotification();
            },

            resetSettings() {
                if (confirm('Сбросить все параметры к значениям по умолчанию?')) {
                    this.settings = JSON.parse(JSON.stringify(this.defaultSettings));
                    this.saveSettings();
                }
            },

            async updateSeasonalityFromHistory() {
                try {
                    this.loading = true;

                    const params = new URLSearchParams({
                        years: this.settings.historyYears
                    });

                    if (this.selectedGroups.length > 0) {
                        params.append('groups', this.selectedGroups.join(','));
                    }

                    const response = await fetch(`/api/calculate-seasonality?${params}`);
                    const data = await response.json();

                    if (data.success && data.seasonality) {
                        // Обновляем коэффициенты сезонности
                        this.settings.seasonality = data.seasonality;

                        // Обновляем график и информацию
                        this.updateSeasonalityInfo();
                        this.createSeasonalityChart();

                        const yearsText = this.settings.historyYears == 1 ? '1 год' : (this.settings.historyYears < 5 ? `${this.settings.historyYears} года` : `${this.settings.historyYears} лет`);
                        console.log(`Коэффициенты сезонности обновлены на основе ${yearsText} истории`);
                    }
                } catch (error) {
                    console.error('Ошибка загрузки коэффициентов сезонности:', error);
                    alert('Ошибка загрузки коэффициентов сезонности. Используются текущие значения.');
                } finally {
                    this.loading = false;
                }
            },

            getMonthNameShort(month) {
                const names = {
                    1: '❄️ Янв', 2: '❄️ Фев', 3: '🌱 Мар', 4: '🌱 Апр',
                    5: '🌸 Май', 6: '☀️ Июн', 7: '☀️ Июл', 8: '☀️ Авг',
                    9: '🍂 Сен', 10: '🍂 Окт', 11: '🍂 Ноя', 12: '❄️ Дек'
                };
                return names[month] || '';
            },

            async loadPrevMonthHistory() {
                try {
                    this.areaHistory = {};

                    // 1. Загружаем данные за предыдущий месяц
                    let prevMonth = this.selectedMonth - 1;
                    let prevYear = this.selectedYear;

                    if (prevMonth < 1) {
                        prevMonth = 12;
                        prevYear -= 1;
                    }

                    const prevStartDate = new Date(prevYear, prevMonth - 1, 1);
                    const prevEndDate = new Date(prevYear, prevMonth, 0);

                    // Helper to format date as YYYY-MM-DD in local timezone (avoids UTC shift)
                    const formatDate = (d) => {
                        const year = d.getFullYear();
                        const month = String(d.getMonth() + 1).padStart(2, '0');
                        const day = String(d.getDate()).padStart(2, '0');
                        return `${year}-${month}-${day}`;
                    };

                    const prevDateFrom = formatDate(prevStartDate);
                    const prevDateTo = formatDate(prevEndDate);

                    // 2. Загружаем данные за тот же месяц прошлого года
                    const lastYearStartDate = new Date(this.selectedYear - 1, this.selectedMonth - 1, 1);
                    const lastYearEndDate = new Date(this.selectedYear - 1, this.selectedMonth, 0);

                    const lastYearDateFrom = formatDate(lastYearStartDate);
                    const lastYearDateTo = formatDate(lastYearEndDate);

                    // 3. Параллельно загружаем оба периода с фильтрами
                    const params1 = new URLSearchParams({
                        date_from: prevDateFrom,
                        date_to: prevDateTo
                    });
                    // Use selectedGroups for sales filtering (sales_groups param)
                    if (this.selectedGroups.length > 0) {
                        params1.append('sales_groups', this.selectedGroups.join(','));
                    }
                    // Use selectedDebtGroups for debt filtering (groups param)
                    if (this.selectedDebtGroups.length > 0) {
                        params1.append('groups', this.selectedDebtGroups.join(','));
                    }

                    const params2 = new URLSearchParams({
                        date_from: lastYearDateFrom,
                        date_to: lastYearDateTo
                    });
                    // Use selectedGroups for sales filtering (sales_groups param)
                    if (this.selectedGroups.length > 0) {
                        params2.append('sales_groups', this.selectedGroups.join(','));
                    }
                    // Use selectedDebtGroups for debt filtering (groups param)
                    if (this.selectedDebtGroups.length > 0) {
                        params2.append('groups', this.selectedDebtGroups.join(','));
                    }
                    console.log('[loadPrevMonthHistory] lastYear request URL:', `/api/sales-areas?${params2}`);

                    const [prevMonthResponse, lastYearResponse] = await Promise.all([
                        fetch(`/api/sales-areas?${params1}`),
                        fetch(`/api/sales-areas?${params2}`)
                    ]);

                    const prevMonthData = await prevMonthResponse.json();
                    const lastYearData = await lastYearResponse.json();

                    // 4. Объединяем данные
                    if (prevMonthData.success) {
                        prevMonthData.data.forEach(area => {
                            this.areaHistory[area.code] = {
                                sales: area.TotalSales || 0,
                                credit: area.PrevMonthDebt || 0  // ДОЛГ за прошлый месяц
                            };
                        });
                    }

                    if (lastYearData.success) {
                        console.log('[loadPrevMonthHistory] lastYearData areas:', lastYearData.data.length);
                        lastYearData.data.forEach(area => {
                            if (!this.areaHistory[area.code]) {
                                this.areaHistory[area.code] = {};
                            }
                            this.areaHistory[area.code].lastYearSales = area.TotalSales || 0;
                            this.areaHistory[area.code].lastYearCredit = area.LastYearDebt || 0;  // ДОЛГ за прошлый год
                            if (area.code === '101') {
                                console.log('[loadPrevMonthHistory] Area 101 lastYearSales:', area.TotalSales);
                            }
                        });
                    }

                    // 5. Загружаем средние за 12 месяцев для расчёта с учётом фильтров
                    const avgParams = new URLSearchParams({
                        month: this.selectedMonth,
                        year: this.selectedYear
                    });
                    if (this.selectedGroups.length > 0) {
                        avgParams.append('groups', this.selectedGroups.join(','));
                    }
                    // Add debt_groups for avgCredit calculation
                    if (this.selectedDebtGroups.length > 0) {
                        avgParams.append('debt_groups', this.selectedDebtGroups.join(','));
                    }

                    const avgResponse = await fetch(`/api/generate-plans?${avgParams}`);
                    const avgData = await avgResponse.json();

                    if (avgData.success) {
                        Object.entries(avgData.data).forEach(([areaCode, planData]) => {
                            if (!this.areaHistory[areaCode]) {
                                this.areaHistory[areaCode] = {};
                            }
                            this.areaHistory[areaCode].avgSales = planData.avg_sales || 0;
                            this.areaHistory[areaCode].avgCredit = planData.avg_credit || 0;
                        });
                    }

                } catch (error) {
                    console.error('Error loading history:', error);
                }
            },

            getPrevMonthName() {
                let prevMonth = this.selectedMonth - 1;
                if (prevMonth < 1) prevMonth = 12;
                return this.getMonthName(prevMonth);
            },

            getPrevMonthYear() {
                let prevYear = this.selectedYear;
                if (this.selectedMonth === 1) prevYear -= 1;
                return prevYear;
            },

            getLastYear() {
                return this.selectedYear - 1;
            },

            createSeasonalityChart() {
                const ctx = document.getElementById('seasonalityChart');
                if (!ctx) return;

                const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
                const seasonalityValues = Object.values(this.settings.seasonality);

                if (seasonalityChart) {
                    seasonalityChart.destroy();
                }

                // Создаем градиенты для столбцов
                const gradient1 = ctx.getContext('2d').createLinearGradient(0, 0, 0, 400);
                gradient1.addColorStop(0, 'rgba(46, 213, 115, 0.9)');
                gradient1.addColorStop(1, 'rgba(0, 184, 148, 0.7)');

                const gradient2 = ctx.getContext('2d').createLinearGradient(0, 0, 0, 400);
                gradient2.addColorStop(0, 'rgba(255, 107, 107, 0.9)');
                gradient2.addColorStop(1, 'rgba(245, 59, 87, 0.7)');

                const gradient3 = ctx.getContext('2d').createLinearGradient(0, 0, 0, 400);
                gradient3.addColorStop(0, 'rgba(255, 195, 18, 0.9)');
                gradient3.addColorStop(1, 'rgba(253, 167, 4, 0.7)');

                // Линия тренда (среднее значение)
                const avgValue = seasonalityValues.length > 0 ? seasonalityValues.reduce((a, b) => a + b, 0) / seasonalityValues.length : 0;

                seasonalityChart = new Chart(ctx, {
                    type: 'bar',
                    plugins: [ChartDataLabels],  // Включаем плагин для этого графика
                    data: {
                        labels: monthNames,
                        datasets: [{
                            label: 'Сезонность',
                            data: seasonalityValues,
                            backgroundColor: seasonalityValues.map(val => {
                                if (val >= 1.2) return gradient1;
                                if (val >= 0.9) return gradient3;
                                return gradient2;
                            }),
                            borderColor: seasonalityValues.map(val => {
                                if (val >= 1.2) return 'rgba(46, 213, 115, 1)';
                                if (val >= 0.9) return 'rgba(255, 195, 18, 1)';
                                return 'rgba(255, 107, 107, 1)';
                            }),
                            borderWidth: 2,
                            borderRadius: 8,
                            borderSkipped: false,
                        },
                        {
                            label: 'Средняя',
                            data: Array(12).fill(avgValue),
                            type: 'line',
                            borderColor: 'rgba(108, 117, 125, 0.8)',
                            backgroundColor: 'rgba(108, 117, 125, 0.1)',
                            borderWidth: 2,
                            borderDash: [5, 5],
                            pointRadius: 0,
                            pointHoverRadius: 0,
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        interaction: {
                            intersect: false,
                            mode: 'index'
                        },
                        plugins: {
                            datalabels: {
                                display: function (context) {
                                    return true;  // Показываем для всех датасетов
                                },
                                color: function (context) {
                                    // Для линии тренда - серый цвет, для столбцов - белый
                                    return context.datasetIndex === 1 ? 'rgba(200, 200, 200, 0.9)' : 'rgba(255, 255, 255, 0.95)';
                                },
                                anchor: function (context) {
                                    // Для линии - center, для столбцов - end
                                    return context.datasetIndex === 1 ? 'center' : 'end';
                                },
                                align: function (context) {
                                    // Для линии - top, для столбцов - top
                                    return 'top';
                                },
                                offset: function (context) {
                                    // Для линии меньший отступ
                                    return context.datasetIndex === 1 ? -20 : 4;
                                },
                                font: function (context) {
                                    return {
                                        size: context.datasetIndex === 1 ? 11 : 12,
                                        weight: context.datasetIndex === 1 ? 'normal' : 'bold'
                                    };
                                },
                                formatter: function (value, context) {
                                    if (typeof value === 'number') {
                                        // Для линии тренда показываем только на первом и последнем месяце
                                        if (context.datasetIndex === 1) {
                                            if (context.dataIndex === 0 || context.dataIndex === 11) {
                                                return 'Ср: ' + value.toFixed(2);
                                            }
                                            return null;  // Скрываем для остальных точек
                                        }
                                        // Для столбцов показываем всегда
                                        return value.toFixed(2);
                                    }
                                    return null;
                                }
                            },
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: 'rgba(255, 255, 255, 0.9)',
                                    font: {
                                        size: 13,
                                        weight: '600'
                                    },
                                    padding: 15,
                                    usePointStyle: true,
                                    pointStyle: 'circle'
                                }
                            },
                            tooltip: {
                                enabled: true,
                                backgroundColor: 'rgba(0, 0, 0, 0.9)',
                                titleColor: 'rgba(255, 255, 255, 1)',
                                bodyColor: 'rgba(255, 255, 255, 0.9)',
                                borderColor: 'rgba(255, 255, 255, 0.2)',
                                borderWidth: 1,
                                padding: 12,
                                displayColors: true,
                                titleFont: {
                                    size: 14,
                                    weight: 'bold'
                                },
                                bodyFont: {
                                    size: 13
                                },
                                callbacks: {
                                    label: function (context) {
                                        if (context.datasetIndex === 0) {
                                            const value = context.parsed.y;
                                            let status = '';
                                            if (value >= 1.2) status = '🔥 Высокий сезон';
                                            else if (value >= 0.9) status = '📊 Средний сезон';
                                            else status = '❄️ Низкий сезон';
                                            return [
                                                `Коэффициент: ${value.toFixed(2)}`,
                                                status,
                                                `Отклонение от среднего: ${((value - avgValue) * 100).toFixed(1)}%`
                                            ];
                                        }
                                        return `Среднее: ${context.parsed.y.toFixed(2)}`;
                                    }
                                }
                            },
                            title: {
                                display: true,
                                text: 'Коэффициенты сезонности по месяцам',
                                color: 'rgba(255, 255, 255, 0.95)',
                                font: {
                                    size: 16,
                                    weight: 'bold'
                                },
                                padding: {
                                    top: 10,
                                    bottom: 20
                                }
                            },
                            subtitle: {
                                display: true,
                                text: `Среднее значение: ${avgValue.toFixed(2)} | 🔥 > 1.2  📊 0.9-1.2  ❄️ < 0.9`,
                                color: 'rgba(255, 255, 255, 0.7)',
                                font: {
                                    size: 11,
                                    style: 'italic'
                                },
                                padding: {
                                    bottom: 15
                                }
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                max: Math.max(...seasonalityValues) * 1.1,
                                grid: {
                                    color: 'rgba(255, 255, 255, 0.08)',
                                    lineWidth: 1,
                                    drawBorder: false
                                },
                                ticks: {
                                    color: 'rgba(255, 255, 255, 0.8)',
                                    font: {
                                        size: 12,
                                        weight: '500'
                                    },
                                    padding: 10,
                                    callback: function (value) {
                                        return value.toFixed(2);
                                    }
                                },
                                border: {
                                    display: false
                                }
                            },
                            x: {
                                grid: {
                                    display: false
                                },
                                ticks: {
                                    color: 'rgba(255, 255, 255, 0.8)',
                                    font: {
                                        size: 11,
                                        weight: '600'
                                    },
                                    padding: 8
                                },
                                border: {
                                    display: false
                                }
                            }
                        },
                        animation: {
                            duration: 1500,
                            easing: 'easeInOutQuart'
                        }
                    }
                });
            }
        }
    }

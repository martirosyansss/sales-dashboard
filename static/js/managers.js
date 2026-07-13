/* managers — page script (extracted from inline <script>). */
    function managersData() {
        return {
            managers: [],
            filteredManagers: [],
            searchQuery: '',
            loading: true,
            totalSales: 0,
            dateFrom: '',
            dateTo: '',
            divisions: [],
            selectedDivisions: [],

            async init() {
                await this.loadDivisions();
                await this.loadSelectedDivisions();
                this.setThisMonth();
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

            async loadSelectedDivisions() {
                try {
                    const response = await fetch('/api/settings/selected-product-groups');
                    const result = await response.json();
                    if (result.success) {
                        this.selectedDivisions = result.data || [];
                    }
                } catch (error) {
                    console.error('Ошибка загрузки выбранных дивизионов:', error);
                }
            },

            selectAllDivisions() {
                this.selectedDivisions = this.divisions.map(d => d.fGROUP);
                this.applyDivisionFilter();
            },

            clearDivisions() {
                this.selectedDivisions = [];
                this.applyDivisionFilter();
            },

            async applyDivisionFilter() {
                try {
                    const response = await fetch('/api/settings/selected-product-groups/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ selectedGroups: this.selectedDivisions })
                    });
                    
                    if (response.ok) {
                        await this.loadManagers();
                    }
                } catch (error) {
                    console.error('Ошибка применения фильтра:', error);
                }
            },

            setThisMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.loadManagers();
            },

            setLastMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth(), 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.loadManagers();
            },

            setThisYear() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), 0, 1);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(today);
                this.loadManagers();
            },

            formatDate(date) {
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            },

            async loadManagers() {
                this.loading = true;
                try {
                    const url = `/api/managers?date_from=${this.dateFrom}&date_to=${this.dateTo}`;
                    const response = await fetch(url);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.managers = result.data;
                        this.filteredManagers = result.data;
                        
                        // Подсчитать общую сумму продаж для расчета долей
                        this.totalSales = this.managers.reduce((sum, m) => sum + m.TotalSales, 0);
                    }
                } catch (error) {
                    console.error('Ошибка загрузки менеджеров:', error);
                } finally {
                    this.loading = false;
                }
            },

            filterManagers() {
                const query = this.searchQuery.toLowerCase();
                this.filteredManagers = this.managers.filter(manager => 
                    manager.fNAME.toLowerCase().includes(query) ||
                    manager.fCODE.toLowerCase().includes(query)
                );
            },

            calculateShare(sales) {
                return this.totalSales > 0 ? (sales / this.totalSales * 100) : 0;
            },

            async showManagerDetail(managerId) {
                const modal = new bootstrap.Modal(document.getElementById('managerDetailModal'));
                modal.show();
                
                // Загрузить детали через второй Alpine компонент
                const detailComponent = Alpine.$data(document.querySelector('#managerDetailModal'));
                if (detailComponent) {
                    await detailComponent.loadDetail(managerId);
                }
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
            }
        }
    }

    function managerDetailData() {
        return {
            detail: {
                info: null,
                sales_by_month: [],
                top_customers: []
            },
            loading: false,
            chart: null,

            async loadDetail(managerId) {
                this.loading = true;
                this.detail = { info: null, sales_by_month: [], top_customers: [] };
                
                try {
                    const response = await fetch(`/api/managers/${managerId}`);
                    const result = await response.json();
                    
                    if (result.success) {
                        this.detail = result.data;
                        
                        // Отрисовать график после загрузки данных
                        this.$nextTick(() => {
                            this.renderChart();
                        });
                    }
                } catch (error) {
                    console.error('Ошибка загрузки деталей:', error);
                } finally {
                    this.loading = false;
                }
            },

            renderChart() {
                const ctx = document.getElementById('managerSalesChart');
                if (!ctx) return;

                if (this.chart) {
                    this.chart.destroy();
                }

                const labels = this.detail.sales_by_month.map(item => item.Month);
                const values = this.detail.sales_by_month.map(item => item.TotalSum);

                this.chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Продажи (AMD)',
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
                                display: true
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        return 'Сумма: ' + formatCurrency(context.parsed.y);
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

            formatNumber(num) {
                return new Intl.NumberFormat('ru-RU').format(num);
            },

            formatCurrency(num) {
                return new Intl.NumberFormat('ru-RU', {
                    style: 'currency',
                    currency: 'AMD',
                    minimumFractionDigits: 0
                }).format(num);
            }
        }
    }

    // Инициализация drag & drop для карточек менеджеров
    document.addEventListener('DOMContentLoaded', () => {
        const container = document.getElementById('managers-container');
        if (container) {
            new Sortable(container, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                handle: '.card-header',
                onEnd: function() {
                    console.log('Порядок карточек изменён');
                }
            });
        }
    });

/* reports — page script (extracted from inline <script>). */
    function reportsData() {
        return {
            loading: true,
            currentDate: new Date().toLocaleDateString('ru-RU', { 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }),
            dateFrom: '',
            dateTo: '',
            managers: [],
            dailyData: [],
            dailyStats: {
                total: 0,
                average: 0,
                max: 0,
                min: 0
            },
            dailyChart: null,
            totals: {
                plan: 0,
                daily_plan: 0,
                fact: 0,
                percent: 0,
                today: 0,
                avg_daily: 0,
                forecast: 0,
                forecast_percent: 0,
                credit_plan: 0,
                credit_fact: 0,
                credit_percent: 0,
                collected: 0,
                salary: 0,
                bonus: 0
            },

            init() {
                this.setThisMonth();
            },

            setThisMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.loadData();
            },

            setLastMonth() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                const lastDay = new Date(today.getFullYear(), today.getMonth(), 0);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(lastDay);
                this.loadData();
            },

            setThisYear() {
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), 0, 1);
                
                this.dateFrom = this.formatDate(firstDay);
                this.dateTo = this.formatDate(today);
                this.loadData();
            },

            formatDate(date) {
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            },

            async loadData() {
                this.loading = true;
                console.log('Loading data from:', this.dateFrom, 'to:', this.dateTo);
                try {
                    // Загрузка данных менеджеров
                    const response = await fetch(`/api/reports/managers?date_from=${this.dateFrom}&date_to=${this.dateTo}`);
                    const result = await response.json();
                    console.log('Managers data:', result);
                    
                    if (result.success) {
                        this.managers = result.data;
                        this.totals = result.totals;
                    } else {
                        console.error('Failed to load managers:', result.error);
                    }

                    // Загрузка дневных продаж
                    const dailyResponse = await fetch(`/api/reports/daily-sales?date_from=${this.dateFrom}&date_to=${this.dateTo}`);
                    const dailyResult = await dailyResponse.json();
                    console.log('Daily sales data:', dailyResult);
                    
                    if (dailyResult.success) {
                        this.dailyData = dailyResult.data;
                        this.dailyStats = dailyResult.stats;
                        this.renderDailyChart();
                    } else {
                        console.error('Failed to load daily sales:', dailyResult.error);
                    }
                } catch (error) {
                    console.error('Ошибка загрузки данных:', error);
                } finally {
                    this.loading = false;
                }
            },

            renderDailyChart() {
                const ctx = document.getElementById('dailySalesChart');
                
                if (this.dailyChart) {
                    this.dailyChart.destroy();
                }

                const labels = this.dailyData.map(d => d.date_short);
                const values = this.dailyData.map(d => d.total_sales);

                this.dailyChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Продажи',
                            data: values,
                            backgroundColor: values.map(v => {
                                if (v === this.dailyStats.max) return 'rgba(25, 135, 84, 0.8)';
                                if (v === this.dailyStats.min) return 'rgba(255, 193, 7, 0.8)';
                                return 'rgba(13, 110, 253, 0.6)';
                            }),
                            borderColor: values.map(v => {
                                if (v === this.dailyStats.max) return 'rgb(25, 135, 84)';
                                if (v === this.dailyStats.min) return 'rgb(255, 193, 7)';
                                return 'rgb(13, 110, 253)';
                            }),
                            borderWidth: 2
                        },
                        {
                            label: 'Среднее',
                            data: Array(labels.length).fill(this.dailyStats.average),
                            type: 'line',
                            borderColor: 'rgb(13, 202, 240)',
                            borderWidth: 2,
                            borderDash: [5, 5],
                            pointRadius: 0,
                            fill: false
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top',
                                labels: {
                                    color: '#fff'
                                }
                            },
                            tooltip: {
                                callbacks: {
                                    label: (context) => {
                                        if (context.dataset.label === 'Среднее') {
                                            return 'Среднее: ' + this.formatCurrency(context.parsed.y);
                                        }
                                        return 'Продажи: ' + this.formatCurrency(context.parsed.y);
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
                                beginAtZero: true,
                                ticks: {
                                    color: '#adb5bd',
                                    callback: (value) => {
                                        return new Intl.NumberFormat('hy-AM').format(value);
                                    }
                                },
                                grid: { color: 'rgba(255, 255, 255, 0.1)' }
                            }
                        }
                    }
                });
            },

            formatCurrency(value) {
                if (!value) return '0 ֏';
                return new Intl.NumberFormat('hy-AM').format(Math.round(value)) + ' ֏';
            }
        }
    }

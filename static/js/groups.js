/* groups — page script (extracted from inline <script>). */
    function groupsData() {
        return {
            groups: [],
            loading: true,
            totalSales: 0,
            totalCustomers: 0,
            totalSalesCount: 0,
            chart: null,

            init() {
                this.loadGroups();
            },

            async loadGroups() {
                this.loading = true;
                try {
                    const response = await fetch('/api/groups');
                    const result = await response.json();
                    
                    if (result.success) {
                        this.groups = result.data;
                        
                        // Подсчитать итоги
                        this.totalSales = this.groups.reduce((sum, g) => sum + g.TotalSales, 0);
                        this.totalCustomers = this.groups.reduce((sum, g) => sum + g.CustomerCount, 0);
                        this.totalSalesCount = this.groups.reduce((sum, g) => sum + g.SalesCount, 0);
                        
                        // Отрисовать график
                        this.$nextTick(() => {
                            this.renderChart();
                        });
                    }
                } catch (error) {
                    console.error('Ошибка загрузки групп:', error);
                } finally {
                    this.loading = false;
                }
            },

            calculateShare(sales) {
                return this.totalSales > 0 ? (sales / this.totalSales * 100) : 0;
            },

            renderChart() {
                const ctx = document.getElementById('groupsChart');
                if (!ctx) return;

                if (this.chart) {
                    this.chart.destroy();
                }

                const labels = this.groups.map(g => 'Группа ' + g.GroupCode);
                const values = this.groups.map(g => g.TotalSales);

                this.chart = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{
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
                                'rgba(255, 152, 0, 0.8)',
                                'rgba(3, 169, 244, 0.8)',
                                'rgba(233, 30, 99, 0.8)',
                                'rgba(139, 195, 74, 0.8)',
                                'rgba(255, 235, 59, 0.8)',
                                'rgba(121, 85, 72, 0.8)',
                                'rgba(158, 158, 158, 0.8)'
                            ]
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                position: 'right'
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        const value = formatCurrency(context.parsed);
                                        const percentage = ((context.parsed / values.reduce((a, b) => a + b, 0)) * 100).toFixed(1);
                                        return `${context.label}: ${value} (${percentage}%)`;
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

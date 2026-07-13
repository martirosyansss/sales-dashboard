/* customer_cards — page script (extracted from inline <script>). */
function customerCardsData() {
    return {
        // State
        allCustomers: [],
        customers: [],
        customerGroups: [],
        loading: false,
        
        // Filters
        dateFrom: '',
        dateTo: '',
        searchQuery: '',
        selectedGroup: '',
        sortBy: 'sales_desc',
        filterType: 'all',
        
        // Summary
        summary: {
            totalSales: 0,
            totalPayments: 0,
            totalDebt: 0,
            avgPaymentRate: 0
        },
        
        // Detail Modal
        detailModal: {
            open: false,
            customer: null,
            chart: null
        },

        init() {
            this.setDefaultDates();
            this.loadCustomerGroups();
            this.loadCustomers();
        },

        setDefaultDates() {
            const today = new Date();
            const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
            
            this.dateFrom = firstDay.toISOString().split('T')[0];
            this.dateTo = today.toISOString().split('T')[0];
        },

        async loadCustomerGroups() {
            try {
                const response = await fetch('/api/customer-groups');
                const data = await response.json();
                if (data.success) {
                    this.customerGroups = data.data;
                }
            } catch (error) {
                console.error('Error loading customer groups:', error);
            }
        },

        async loadCustomers() {
            this.loading = true;
            try {
                const params = new URLSearchParams({
                    date_from: this.dateFrom,
                    date_to: this.dateTo
                });

                const response = await fetch(`/api/distributors?${params}`);
                const data = await response.json();
                
                if (data.success) {
                    this.allCustomers = data.data;
                    this.filterCustomers();
                    this.calculateSummary();
                }
            } catch (error) {
                console.error('Error loading customers:', error);
            } finally {
                this.loading = false;
            }
        },

        filterCustomers() {
            let filtered = [...this.allCustomers];

            // Search filter
            if (this.searchQuery) {
                const query = this.searchQuery.toLowerCase();
                filtered = filtered.filter(c => 
                    c.CustomerCode.toLowerCase().includes(query) ||
                    c.CustomerName.toLowerCase().includes(query)
                );
            }

            // Group filter
            if (this.selectedGroup) {
                filtered = filtered.filter(c => c.GroupCode === this.selectedGroup);
            }

            // Type filter
            switch (this.filterType) {
                case 'high_sales':
                    const avgSales = this.allCustomers.reduce((sum, c) => sum + c.TotalSales, 0) / this.allCustomers.length;
                    filtered = filtered.filter(c => c.TotalSales > avgSales * 1.5);
                    break;
                case 'high_debt':
                    filtered = filtered.filter(c => c.TotalDebt > c.TotalSales * 0.3);
                    break;
                case 'good_payers':
                    filtered = filtered.filter(c => c.PaymentRate >= 80);
                    break;
                case 'problematic':
                    filtered = filtered.filter(c => c.PaymentRate < 50 && c.TotalDebt > 0);
                    break;
            }

            this.customers = filtered;
            this.sortCustomers();
        },

        sortCustomers() {
            switch (this.sortBy) {
                case 'sales_desc':
                    this.customers.sort((a, b) => b.TotalSales - a.TotalSales);
                    break;
                case 'sales_asc':
                    this.customers.sort((a, b) => a.TotalSales - b.TotalSales);
                    break;
                case 'debt_desc':
                    this.customers.sort((a, b) => b.TotalDebt - a.TotalDebt);
                    break;
                case 'debt_asc':
                    this.customers.sort((a, b) => a.TotalDebt - b.TotalDebt);
                    break;
                case 'payment_rate':
                    this.customers.sort((a, b) => b.PaymentRate - a.PaymentRate);
                    break;
                case 'name':
                    this.customers.sort((a, b) => a.CustomerName.localeCompare(b.CustomerName));
                    break;
            }
        },

        setFilterType(type) {
            this.filterType = type;
            this.filterCustomers();
        },

        calculateSummary() {
            this.summary.totalSales = this.customers.reduce((sum, c) => sum + c.TotalSales, 0);
            this.summary.totalPayments = this.customers.reduce((sum, c) => sum + c.TotalPayments, 0);
            this.summary.totalDebt = this.customers.reduce((sum, c) => sum + c.TotalDebt, 0);
            
            if (this.customers.length > 0) {
                this.summary.avgPaymentRate = this.customers.reduce((sum, c) => sum + c.PaymentRate, 0) / this.customers.length;
            }
        },

        openDetailModal(customer) {
            this.detailModal.customer = customer;
            this.detailModal.open = true;
            
            this.$nextTick(() => {
                this.renderDetailChart();
            });
        },

        closeDetailModal() {
            if (this.detailModal.chart) {
                this.detailModal.chart.destroy();
                this.detailModal.chart = null;
            }
            this.detailModal.open = false;
            this.detailModal.customer = null;
        },

        renderDetailChart() {
            const canvas = document.getElementById('customerDetailChart');
            if (!canvas) return;

            const ctx = canvas.getContext('2d');
            const customer = this.detailModal.customer;

            if (this.detailModal.chart) {
                this.detailModal.chart.destroy();
            }

            this.detailModal.chart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Продажи', 'Платежи', 'Задолженность'],
                    datasets: [{
                        label: 'Сумма (֏)',
                        data: [
                            customer.TotalSales,
                            customer.TotalPayments,
                            Math.abs(customer.TotalDebt)
                        ],
                        backgroundColor: [
                            'rgba(13, 110, 253, 0.7)',
                            'rgba(25, 135, 84, 0.7)',
                            customer.TotalDebt > 0 ? 'rgba(220, 53, 69, 0.7)' : 'rgba(25, 135, 84, 0.7)'
                        ],
                        borderColor: [
                            '#0d6efd',
                            '#198754',
                            customer.TotalDebt > 0 ? '#dc3545' : '#198754'
                        ],
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            callbacks: {
                                label: (context) => {
                                    return this.formatCurrency(context.parsed.y) + ' ֏';
                                }
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
        },

        resetFilters() {
            this.searchQuery = '';
            this.selectedGroup = '';
            this.sortBy = 'sales_desc';
            this.filterType = 'all';
            this.setDefaultDates();
            this.loadCustomers();
        },

        exportToExcel() {
            const data = this.customers.map(c => ({
                'Код': c.CustomerCode,
                'Название': c.CustomerName,
                'Группа': c.GroupCode,
                'Продажи': c.TotalSales,
                'Платежи': c.TotalPayments,
                'Долг': c.TotalDebt,
                '% оплаты': Math.round(c.PaymentRate),
                'Сделок': c.TransactionCount,
                'Средний чек': Math.round(c.AvgSale)
            }));

            const ws = XLSX.utils.json_to_sheet(data);
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, ws, 'Клиенты');
            XLSX.writeFile(wb, `Клиенты_${this.dateFrom}_${this.dateTo}.xlsx`);
        },

        formatCurrency(value) {
            if (!value) return '0';
            return Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
        }
    };
}

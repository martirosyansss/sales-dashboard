/* distributors — page script (extracted from inline <script>). */
    let allData = [];
    let filteredData = [];
    let currentSort = { field: 'sales', direction: 'desc' };
    let currentPage = 1;
    const itemsPerPage = 12;
    let topSalesChart = null;
    let salesVsPaymentsChart = null;
    let detailChart = null;
    
    document.addEventListener('DOMContentLoaded', function() {
        initializeDates();
        loadFilters();
        loadData();
        
        document.getElementById('searchInput').addEventListener('input', debounce(filterData, 300));
        
        document.getElementById('detailModal').addEventListener('click', function(e) {
            if (e.target === this) closeDetailModal();
        });
        
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeDetailModal();
        });
    });
    
    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func(...args), wait);
        };
    }
    
    function initializeDates() {
        const today = new Date();
        const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
        document.getElementById('dateFrom').value = firstDay.toISOString().split('T')[0];
        document.getElementById('dateTo').value = today.toISOString().split('T')[0];
    }
    
    function loadFilters() {
        fetch('/api/product-groups')
            .then(r => r.json())
            .then(response => {
                const select = document.getElementById('divisionFilter');
                const data = response.data || response || [];
                data.forEach(d => {
                    const opt = document.createElement('option');
                    opt.value = d.code || d.id;
                    opt.textContent = d.name || d.code;
                    select.appendChild(opt);
                });
            })
            .catch(err => console.error('Error loading divisions:', err));
        
        fetch('/api/customer-groups')
            .then(r => r.json())
            .then(response => {
                const container = document.getElementById('groupsFilter');
                const data = response.data || response || [];
                container.innerHTML = data.map(g => `
                    <label class="filter-checkbox-item">
                        <input type="checkbox" value="${g.code || g.id}" checked>
                        ${g.name || g.code || g.id}
                    </label>
                `).join('');
            })
            .catch(err => console.error('Error loading groups:', err));
    }
    
    function selectAllGroups() {
        document.querySelectorAll('#groupsFilter input').forEach(cb => cb.checked = true);
    }
    
    function deselectAllGroups() {
        document.querySelectorAll('#groupsFilter input').forEach(cb => cb.checked = false);
    }
    
    function getSelectedGroups() {
        return Array.from(document.querySelectorAll('#groupsFilter input:checked')).map(cb => cb.value);
    }
    
    function showLoading() {
        document.getElementById('loadingOverlay').classList.add('active');
    }
    
    function hideLoading() {
        document.getElementById('loadingOverlay').classList.remove('active');
    }
    
    function loadData() {
        showLoading();
        
        const params = new URLSearchParams({
            date_from: document.getElementById('dateFrom').value,
            date_to: document.getElementById('dateTo').value
        });
        
        const division = document.getElementById('divisionFilter').value;
        if (division) params.append('division', division);
        
        const groups = getSelectedGroups();
        if (groups.length > 0) params.append('groups', groups.join(','));
        
        fetch(`/api/distributors?${params}`)
            .then(r => r.json())
            .then(response => {
                const rawData = response.data || response || [];
                allData = rawData.map(item => ({
                    code: item.CustomerCode || item.code || '',
                    name: item.CustomerName || item.name || '',
                    group: item.CustomerGroup || item.group || '',
                    sales: item.TotalSales || item.sales || 0,
                    payments: item.TotalPayments || item.payments || 0,
                    debt: item.TotalDebt || item.debt || 0,
                    debt_from_docs: item.DebtFromDocs || item.debt_from_docs || 0,
                    type01: item.Type01 || item.type01 || 0,
                    type02: item.Type02 || item.type02 || 0,
                    sales_count: item.SalesCount || item.sales_count || 0,
                    payment_rate: item.TotalSales > 0 ? (item.TotalPayments / item.TotalSales * 100) : 0
                }));
                filterData();
                updateSummary(allData);
                updateCharts();
                hideLoading();
            })
            .catch(err => {
                console.error('Error loading data:', err);
                hideLoading();
            });
    }
    
    function filterData() {
        const searchTerm = document.getElementById('searchInput').value.toLowerCase();
        filteredData = allData.filter(item => {
            return !searchTerm ||
                (item.code && item.code.toLowerCase().includes(searchTerm)) ||
                (item.name && item.name.toLowerCase().includes(searchTerm));
        });
        sortData(currentSort.field, false);
        updateSummary(filteredData);
    }
    
    function sortData(field, toggle = true) {
        if (toggle) {
            if (currentSort.field === field) {
                currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort.field = field;
                currentSort.direction = 'desc';
            }
        }
        
        filteredData.sort((a, b) => {
            let valA = a[field] || (typeof a[field] === 'string' ? '' : 0);
            let valB = b[field] || (typeof b[field] === 'string' ? '' : 0);
            
            if (field === 'rate') {
                valA = a.payment_rate || 0;
                valB = b.payment_rate || 0;
            }
            
            if (typeof valA === 'string') {
                return currentSort.direction === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            }
            return currentSort.direction === 'asc' ? valA - valB : valB - valA;
        });
        
        currentPage = 1;
        renderData();
        updateSortIndicators();
    }
    
    function updateSortIndicators() {
        document.querySelectorAll('.data-table th').forEach((th, i) => {
            th.classList.remove('sorted');
            const icon = th.querySelector('i');
            if (icon) icon.className = 'fas fa-sort';
        });
        
        const fields = ['code', 'name', 'group', 'sales', 'payments', 'debt', 'rate'];
        const idx = fields.indexOf(currentSort.field);
        if (idx >= 0) {
            const th = document.querySelectorAll('.data-table th')[idx];
            th.classList.add('sorted');
            const icon = th.querySelector('i');
            if (icon) icon.className = `fas fa-sort-${currentSort.direction === 'asc' ? 'up' : 'down'}`;
        }
    }
    
    function renderData() {
        const start = (currentPage - 1) * itemsPerPage;
        const pageData = filteredData.slice(start, start + itemsPerPage);
        
        document.getElementById('distributorsGrid').innerHTML = pageData.map(item => createCard(item)).join('');
        document.getElementById('tableBody').innerHTML = pageData.map(item => createRow(item)).join('');
        renderPagination();
    }
    
    function createCard(item) {
        const rate = item.payment_rate || 0;
        let rateClass = 'danger';
        if (rate >= 90) rateClass = 'excellent';
        else if (rate >= 70) rateClass = 'good';
        else if (rate >= 50) rateClass = 'warning';
        
        const debtClass = (item.debt || 0) <= 0 ? 'positive' : '';
        
        return `
            <div class="distributor-card" onclick='showDetail(${JSON.stringify(item).replace(/'/g, "&#39;")})'>
                <div class="distributor-header">
                    <div class="distributor-code">Код: ${item.code || '-'}</div>
                    <div class="distributor-name" title="${item.name || ''}">${item.name || 'Без названия'}</div>
                    <div class="distributor-group">${item.group || 'Без группы'}</div>
                </div>
                <div class="distributor-body">
                    <div class="metric-row">
                        <span class="metric-label"><i class="fas fa-shopping-cart"></i> Продажи</span>
                        <span class="metric-value sales">${formatCurrency(item.sales)}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label"><i class="fas fa-credit-card"></i> Оплаты</span>
                        <span class="metric-value payments">${formatCurrency(item.payments)}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label"><i class="fas fa-exclamation-triangle"></i> Долг</span>
                        <span class="metric-value debt ${debtClass}">${formatCurrency(item.debt)}</span>
                    </div>
                    <div class="payment-rate-container">
                        <div class="payment-rate-header">
                            <span>Процент оплаты</span>
                            <span>${rate.toFixed(1)}%</span>
                        </div>
                        <div class="payment-rate-bar">
                            <div class="payment-rate-fill ${rateClass}" style="width: ${Math.min(rate, 100)}%"></div>
                        </div>
                    </div>
                    <button class="detail-btn" onclick="event.stopPropagation(); showDetail(${JSON.stringify(item).replace(/'/g, "&#39;")})">
                        <i class="fas fa-chart-line me-1"></i>Детальный анализ
                    </button>
                </div>
            </div>
        `;
    }
    
    function createRow(item) {
        const rate = item.payment_rate || 0;
        let rateClass = '';
        if (rate >= 90) rateClass = 'text-success';
        else if (rate >= 70) rateClass = 'text-warning';
        else if (rate < 50) rateClass = 'text-danger';
        
        const debtClass = (item.debt || 0) <= 0 ? 'text-success' : 'text-danger';
        
        return `
            <tr onclick='showDetail(${JSON.stringify(item).replace(/'/g, "&#39;")})'>
                <td>${item.code || '-'}</td>
                <td>${item.name || '-'}</td>
                <td>${item.group || '-'}</td>
                <td class="text-success fw-semibold">${formatCurrency(item.sales)}</td>
                <td class="text-warning fw-semibold">${formatCurrency(item.payments)}</td>
                <td class="${debtClass} fw-semibold">${formatCurrency(item.debt)}</td>
                <td class="${rateClass} fw-semibold">${rate.toFixed(1)}%</td>
            </tr>
        `;
    }
    
    function renderPagination() {
        const totalPages = Math.ceil(filteredData.length / itemsPerPage);
        const container = document.getElementById('paginationContainer');
        
        if (totalPages <= 1) {
            container.innerHTML = '';
            return;
        }
        
        let html = `
            <button class="pagination-btn" onclick="changePage(1)" ${currentPage === 1 ? 'disabled' : ''}>
                <i class="fas fa-angle-double-left"></i>
            </button>
            <button class="pagination-btn" onclick="changePage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>
                <i class="fas fa-angle-left"></i>
            </button>
        `;
        
        const maxVisible = 5;
        let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
        let endPage = Math.min(totalPages, startPage + maxVisible - 1);
        if (endPage - startPage < maxVisible - 1) startPage = Math.max(1, endPage - maxVisible + 1);
        
        for (let i = startPage; i <= endPage; i++) {
            html += `<button class="pagination-btn ${i === currentPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
        }
        
        html += `
            <button class="pagination-btn" onclick="changePage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>
                <i class="fas fa-angle-right"></i>
            </button>
            <button class="pagination-btn" onclick="changePage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''}>
                <i class="fas fa-angle-double-right"></i>
            </button>
            <span class="pagination-info">${currentPage} из ${totalPages} (${filteredData.length} записей)</span>
        `;
        
        container.innerHTML = html;
    }
    
    function changePage(page) {
        currentPage = page;
        renderData();
    }
    
    function switchView(view) {
        if (view === 'cards') {
            document.getElementById('cardsView').classList.remove('hidden');
            document.getElementById('tableView').classList.remove('active');
            document.getElementById('cardsViewBtn').classList.add('active');
            document.getElementById('tableViewBtn').classList.remove('active');
        } else {
            document.getElementById('cardsView').classList.add('hidden');
            document.getElementById('tableView').classList.add('active');
            document.getElementById('cardsViewBtn').classList.remove('active');
            document.getElementById('tableViewBtn').classList.add('active');
        }
    }
    
    function updateSummary(data = allData) {
        let total = { customers: data.length, sales: 0, payments: 0, debt: 0 };
        data.forEach(item => {
            total.sales += item.sales || 0;
            total.payments += item.payments || 0;
            total.debt += item.debt || 0;
        });
        
        document.getElementById('totalCustomers').textContent = total.customers.toLocaleString('ru-RU');
        document.getElementById('totalSales').textContent = formatCurrency(total.sales);
        document.getElementById('totalPayments').textContent = formatCurrency(total.payments);
        document.getElementById('totalDebt').textContent = formatCurrency(total.debt);
    }
    
    function updateCharts() {
        const top10 = [...allData].sort((a, b) => (b.sales || 0) - (a.sales || 0)).slice(0, 10);
        const labels = top10.map(item => item.code || 'N/A');
        const salesData = top10.map(item => item.sales || 0);
        const paymentsData = top10.map(item => item.payments || 0);
        
        if (topSalesChart) topSalesChart.destroy();
        if (salesVsPaymentsChart) salesVsPaymentsChart.destroy();
        
        const pieCtx = document.getElementById('topSalesChart').getContext('2d');
        topSalesChart = new Chart(pieCtx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: salesData,
                    backgroundColor: ['#0d6efd', '#198754', '#ffc107', '#dc3545', '#0dcaf0', '#6f42c1', '#fd7e14', '#20c997', '#6c757d', '#adb5bd'],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'right', labels: { color: '#adb5bd', boxWidth: 12, padding: 8, font: { size: 11 } } },
                    tooltip: { callbacks: { label: ctx => ctx.label + ': ' + formatCurrency(ctx.raw) } }
                }
            }
        });
        
        const barCtx = document.getElementById('salesVsPaymentsChart').getContext('2d');
        salesVsPaymentsChart = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    { label: 'Продажи', data: salesData, backgroundColor: 'rgba(25, 135, 84, 0.8)', borderRadius: 4 },
                    { label: 'Оплаты', data: paymentsData, backgroundColor: 'rgba(255, 193, 7, 0.8)', borderRadius: 4 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: '#adb5bd' } },
                    tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + formatCurrency(ctx.raw) } }
                },
                scales: {
                    x: { ticks: { color: '#6c757d' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { ticks: { color: '#6c757d', callback: v => formatShort(v) }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });
    }
    
    function showDetail(item) {
        document.getElementById('detailTitle').textContent = item.name || 'Без названия';
        document.getElementById('detailSubtitle').textContent = `Код: ${item.code || '-'} | Группа: ${item.group || '-'}`;
        
        document.getElementById('detailSales').textContent = formatCurrency(item.sales);
        document.getElementById('detailPayments').textContent = formatCurrency(item.payments);
        document.getElementById('detailDebt').textContent = formatCurrency(item.debt);
        document.getElementById('detailRate').textContent = (item.payment_rate || 0).toFixed(1) + '%';
        
        document.getElementById('detailDebtFromDocs').textContent = formatCurrency(item.debt_from_docs);
        document.getElementById('detailType01').textContent = '-' + formatCurrency(Math.abs(item.type01 || 0));
        document.getElementById('detailType02').textContent = '-' + formatCurrency(Math.abs(item.type02 || 0));
        document.getElementById('detailTotalDebt').textContent = formatCurrency(item.debt);
        
        document.getElementById('detailSalesCount').textContent = (item.sales_count || 0).toLocaleString('ru-RU');
        const avgSale = item.sales_count > 0 ? item.sales / item.sales_count : 0;
        document.getElementById('detailAvgSale').textContent = formatCurrency(avgSale);
        
        if (detailChart) detailChart.destroy();
        const ctx = document.getElementById('detailChart').getContext('2d');
        detailChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['Продажи', 'Оплаты', 'Долг'],
                datasets: [{
                    data: [item.sales || 0, item.payments || 0, Math.abs(item.debt || 0)],
                    backgroundColor: ['rgba(25, 135, 84, 0.8)', 'rgba(255, 193, 7, 0.8)', 'rgba(220, 53, 69, 0.8)'],
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => formatCurrency(ctx.raw) } } },
                scales: {
                    x: { ticks: { color: '#adb5bd' }, grid: { display: false } },
                    y: { ticks: { color: '#6c757d', callback: v => formatShort(v) }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });
        
        document.getElementById('detailModal').classList.add('active');
        document.body.style.overflow = 'hidden';
    }
    
    function closeDetailModal() {
        document.getElementById('detailModal').classList.remove('active');
        document.body.style.overflow = '';
    }
    
    function formatCurrency(value) {
        return new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(value || 0) + ' ֏';
    }
    
    function formatShort(value) {
        if (value >= 1000000) return (value / 1000000).toFixed(1) + 'М';
        if (value >= 1000) return (value / 1000).toFixed(0) + 'К';
        return value.toString();
    }

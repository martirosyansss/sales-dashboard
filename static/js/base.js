/* Shared number/currency/percent formatting helpers
   Extracted from base_v2.html during frontend refactor. */
function formatNumber(num) {
    return new Intl.NumberFormat('ru-RU').format(num);
}

function formatCurrency(num) {
    return new Intl.NumberFormat('ru-RU', {
        style: 'currency',
        currency: 'AMD',
        minimumFractionDigits: 0
    }).format(num);
}

function formatPercent(num) {
    return (num >= 0 ? '+' : '') + num.toFixed(1) + '%';
}

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

/* «Բեռնագիր» (ответ владельца №57/№62) — печатный лист машины на день, общий у «Развоза» (/routes/dispatch) и склада
   «Պահեստ» (/routes/warehouse): один рендер — один документ. Здесь, а не в своём файле: base.js уже грузят обе страницы
   (base_v2.html) и он открыт снаружи (araqich.orix.am) — без новых правил туннеля и nginx. Вход: ответ
   /api/routes/dispatch/waybill или /api/routes/warehouse/waybill (wb), машина плана t {car_code, name}, день
   d {day, weekday}. Всё с сервера — через esc. */
window.RtWaybill = (function () {
    'use strict';
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const NB = ' ';
    const pl = (n, word) => fmt(n) + NB + word;
    const WD_NAME = { 1: 'երկուշաբթի', 2: 'երեքշաբթի', 3: 'չորեքշաբթի', 4: 'հինգշաբթի', 5: 'ուրբաթ', 6: 'շաբաթ', 7: 'կիրակի' };
    const hhmm = (m) => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
    const dateRu = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    const truckLabel = (t) => (t.name ? t.name + ' · ' : '') + t.car_code;
    // №90: подарки ERP (SALEDOCGIFTS) уже в количестве строки — gift: сколько из них подарки, склад грузит всё
    const wbGift = (r) => (num(r.gift) ? ' · այդ թվում՝ ' + fmt(r.gift, 4) + NB + 'նվեր' : '');
    const wbName = (r) => (r.unknown ? 'ERP-ում անհայտ ապրանք (ID ' + r.product_id + ')' : (r.name || '—')) + wbGift(r);
    const wbQty = (r) => fmt(r.qty, 4) + (r.unit ? NB + r.unit : '');
    // «12 փաթեթ + 3 հատ» — для склада: упаковка «փաթեթ» из доп. единицы товара ERP
    const wbPacks = (r) => !r.pack || r.packs === null ? '' : [r.packs ? fmt(r.packs) + NB + 'փաթեթ' : '',
        r.loose ? fmt(r.loose) + NB + (r.unit || 'հատ') : ''].filter(Boolean).join(' + ');
    function wbNotes(tr) {
        const out = [];
        if (tr.orders && tr.invoiced === tr.orders) out.push('Քանակները՝ ERP-ի ապրանքագրերից։');
        else if (!tr.invoiced) out.push('Քանակները՝ պատվերներից․ ապրանքագրեր դեռ չկան։');
        else out.push('Քանակները՝ ' + pl(tr.invoiced, 'պատվեր') + '՝ ապրանքագրից, ' + fmt(tr.orders - tr.invoiced) + '՝ պատվերից (ապրանքագիր դեռ չկա)։');
        if (tr.split) out.push('Ներառում է ' + fmt(tr.split) + NB + 'խանութի մեծ պատվերի մասը․ այդ պատվերները տարվում են մի քանի երթով։');
        if (tr.mixed) out.push('Ուշադրություն՝ ' + fmt(tr.mixed) + NB + 'պատվերի ապրանքագրում կա նաև պատվեր, որը այս երթերում չէ'
            + ' (օրինակ՝ «այսօր չենք տանում»)․ ապրանքագրի ամբողջ ապրանքը հաշվված է այստեղ — ստուգեք քանակները։');
        if (tr.unknown) out.push('Ուշադրություն՝ ' + pl(tr.unknown, 'ապրանք') + ' ERP-ի ցուցակում չի գտնվել (նշված է ID-ով)։');
        return out;
    }
    // «Բեռնման հերթականություն» (ответ владельца №87 п. 4): магазины рейса в обратном порядке объезда (tr.loading с
    // сервера) — последний магазин грузится первым, в глубину кузова; итоги рейса выше не меняются
    const LOAD_HINT = 'Վերջին խանութի ապրանքը բեռնել առաջինը՝ թափքի խորքում, առաջին խանութինը՝ վերջինը՝ դռան մոտ։';
    const kgText = (kg) => '≈' + NB + fmt(kg) + NB + 'կգ';
    const loadNo = (x) => String(x.no) + (x.no === 1 ? ' — բեռնել առաջինը' : '');
    // складу сервер магазин не отдаёт (решение владельца №87) — только «Կետ N» (номер точки в объезде)
    const loadStore = (x) => (x.name || x.code ? (x.name || x.code) + (x.name && x.code ? ' (' + x.code + ')' : '') + ' · կետ № ' + x.stop
        : 'Կետ ' + x.stop) + (x.split ? ' · մեծ պատվերի մաս' : '');
    function loadingHtml(tr) {
        const list = Array.isArray(tr.loading) ? tr.loading : [];
        if (!list.length) return '';
        let h = '<h2 class="ld">Բեռնման հերթականություն</h2><p class="ld-hint">' + esc(LOAD_HINT) + '</p>'
            + '<table><thead><tr><th>Բեռն. №</th><th>Կոդ</th><th>Ապրանք</th><th>Քանակ</th><th>Փաթեթ</th><th>✓</th></tr></thead>';
        list.forEach(x => {
            h += '<tbody class="ld-g"><tr class="ld-h"><td class="n">' + esc(x.no) + '</td><td colspan="5"><b>' + esc(loadStore(x)) + '</b> · '
                + esc(kgText(x.kg)) + (x.no === 1 ? ' · <b>բեռնել առաջինը</b>' : '') + '</td></tr>';
            (x.rows || []).forEach(r => {
                h += '<tr><td></td><td class="c">' + esc(r.code) + '</td><td>' + esc(wbName(r)) + '</td><td class="q">' + esc(wbQty(r)) + '</td>'
                    + '<td class="p">' + esc(wbPacks(r)) + '</td><td class="ok"></td></tr>';
            });
            if (!(x.rows || []).length) h += '<tr><td></td><td colspan="5">Ապրանքներ չկան։</td></tr>';
            h += '</tbody>';
        });
        return h + '</table>';
    }
    function waybillHtml(t, d, wb) {
        const dayText = (WD_NAME[d.weekday] || '') + ', ' + dateRu(d.day);
        const made = new Date();
        const madeText = dateRu(made.getFullYear() + '-' + String(made.getMonth() + 1).padStart(2, '0') + '-' + String(made.getDate()).padStart(2, '0'))
            + ' ' + hhmm(made.getHours() * 60 + made.getMinutes());
        let html = '<!doctype html><html lang="hy"><head><meta charset="utf-8"><title>Բեռնագիր ' + esc(t.car_code) + ' ' + esc(dateRu(d.day)) + '</title><style>'
            + 'body{font-family:"Segoe UI",Sylfaen,"Noto Sans Armenian",Arial,sans-serif;color:#000;margin:0;padding:12mm;font-size:15px}'
            + '.sheet{page-break-after:always;break-after:page}.sheet:last-child{page-break-after:auto;break-after:auto}'
            + 'h1{font-size:24px;margin:0 0 4px;letter-spacing:.04em}.sub{font-size:15px;margin:0 0 4px}.sub b{font-size:17px}'
            + 'table{width:100%;border-collapse:collapse;margin-top:10px}th,td{border:1px solid #000;padding:6px 8px;vertical-align:top;text-align:left}'
            + 'th{font-size:13px;background:#eee}td.n{width:30px;text-align:center}td.c{width:64px;white-space:nowrap}'
            + 'td.q{font-size:17px;font-weight:700;white-space:nowrap;text-align:right}td.p{white-space:nowrap}td.p small{color:#444}'
            + 'td.kg{white-space:nowrap;text-align:right;width:80px}td.ok{width:34px}tfoot td{font-weight:700}'
            + '.notes{margin:10px 0 0;padding-left:18px;font-size:13px}.sign{display:flex;gap:40px;margin-top:28px;font-size:15px}'
            + '.sign div{flex:1;display:flex;flex-direction:column;justify-content:flex-end}.sign span{display:block;border-bottom:1px solid #000;height:26px}.made{margin-top:14px;font-size:12px;color:#444}'
            + '.blank{display:inline-block;width:260px;border-bottom:1px solid #000;height:15px;vertical-align:bottom}'
            + 'h2.ld{font-size:19px;margin:20px 0 2px}.ld-hint{font-size:13px;margin:0}tbody.ld-g{break-inside:avoid;page-break-inside:avoid}'
            + 'tr.ld-h td{background:#f2f2f2}tr.ld-h td.n{font-size:20px;font-weight:700}'
            + '@media screen{body{background:#fff}}'
            + '</style></head><body>';
        wb.trips.forEach(tr => {
            html += '<section class="sheet"><h1>ԲԵՌՆԱԳԻՐ</h1>'
                + '<p class="sub"><b>' + esc(truckLabel(t)) + '</b> · ' + esc(dayText) + ' · Երթ ' + tr.no + (wb.trips.length > 1 ? ' / ' + wb.trips.length : '') + '</p>'
                + '<p class="sub">Բեռնում՝ ' + esc(tr.loading_start) + ' · մեկնում՝ ' + esc(tr.depart) + ' · ' + esc(pl(tr.stops, 'խանութ')) + '</p>'
                + '<p class="sub">Վարորդ՝ ' + (wb.driver ? '<b>' + esc(wb.driver) + '</b>' + (wb.driver_seat ? ' (փոխարինում)' : '') : '<span class="blank"></span>')
                + (wb.helper ? ' · Առաքիչ՝ <b>' + esc(wb.helper) + '</b>' : '') + '</p>'
                + '<table><thead><tr><th>№</th><th>Կոդ</th><th>Ապրանք</th><th>Քանակ</th><th>Փաթեթ</th><th>Քաշ, կգ</th><th>✓</th></tr></thead><tbody>';
            tr.rows.forEach((r, i) => {
                html += '<tr><td class="n">' + (i + 1) + '</td><td class="c">' + esc(r.code) + '</td><td>' + esc(wbName(r)) + '</td>'
                    + '<td class="q">' + esc(wbQty(r)) + '</td><td class="p">' + esc(wbPacks(r))
                    + (r.pack && r.packs !== null ? ' <small>(' + esc(r.pack) + '-ական)</small>' : '') + '</td>'
                    + '<td class="kg">' + esc(fmt(r.kg, 1)) + '</td><td class="ok"></td></tr>';
            });
            if (!tr.rows.length) html += '<tr><td colspan="7">Ապրանքներ չկան՝ պատվերներում տողեր չեն գտնվել։</td></tr>';
            html += '</tbody><tfoot><tr><td colspan="5">Ընդամենը՝ ' + esc(pl(tr.rows.length, 'ապրանք')) + '</td><td class="kg">' + esc(fmt(tr.kg))
                + '</td><td></td></tr></tfoot></table><ul class="notes">' + wbNotes(tr).map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>'
                + loadingHtml(tr)
                + '<div class="sign"><div>Բաց թողեց (պահեստապետ)<span></span></div><div>Ընդունեց (վարորդ)'
                + (wb.driver ? '՝ ' + esc(wb.driver) : '') + '<span></span></div>'
                + (wb.helper ? '<div>Ընդունեց (առաքիչ)՝ ' + esc(wb.helper) + '<span></span></div>' : '') + '</div>'
                + '<p class="made">Կազմվել է՝ ' + esc(madeText) + ' · պլան № ' + esc(wb.rev) + '</p></section>';
        });
        return html + '</body></html>';
    }
    // name, notes и подписи блока погрузки — ещё и Excel «Развоза» (те же строки, что на листе)
    return { html: waybillHtml, name: wbName, notes: wbNotes, loadNo, loadStore, loadHint: LOAD_HINT };
})();

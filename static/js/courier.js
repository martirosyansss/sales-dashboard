/* «Առաքիչ» /courier — офис терминалов водителей (docs/plans/courier-app-plan.md §4).
   Вкладки: «Վարորդներ» (водители, PIN, терминалы и QR), «Առաքում այսօր» (по машинам), «Գումար» (деньги водителей,
   «сдал фактически»), «Մակնշում» (коды маркировки, CSV/Excel), «Կարգավորումներ» (маркируемые товары, тара, причины, APK).
   API: /api/courier/admin/* (только admin; POST — JSON). Всё, что пришло с сервера (имена, коды, магазины), выводится
   только через esc() или textContent. QR — SVG, построенный сервером (segno), вставляется как есть. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const money = (v) => fmt(v, 2) + ' ֏';
    const today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
    const timeOf = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(11, 16) : '—');
    const dateTime = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(8, 10) + '.' + s.slice(5, 7) + ' ' + s.slice(11, 16) : '—');

    const STATUS = { full: ['Ստացված է', 'b-ok'], partial: ['Մասնակի', 'b-warn'], refuse: ['Հրաժարում', 'b-danger'], pending: ['Սպասում է', 'b-none'] };
    const COLLECT = { cash: 'Կանխիկ', cash_ecr: 'Կանխիկ ՀԴՄ', none: 'Չվերցնել', ask: 'Ճշտել' };
    const TYPE = { delivery: 'Առաքում', payment: 'Գումար', tare: 'Տարա', return: 'Վերադարձ', scan: 'Սկան', scan_cancel: 'Սկանի չեղարկում',
        unreadable: 'Կոդը չի կարդացվում', arrived: 'Ժամանում', day_closed: 'Օրվա ավարտ' };
    const FLAG = {
        foreign: 'Այլ մեքենայի կամ օրվա կետ', unknown_stop: 'Անհայտ կետ', duplicate_elsewhere: 'Կոդն արդեն տրվել է այլ տեղ',
        repeat: 'Կրկնակի սկան', scan_short: 'Մակնշման սկանը պակաս է', no_ecr_receipt: 'ՀԴՄ կտրոնի համարը չկա',
        paid_collect_none: 'Գումար է վերցվել, թեև պետք չէր', lines_incomplete: 'Ոչ բոլոր տողերն են նշված',
        unknown_line: 'Անհայտ տող', gtin_not_in_invoice: 'GTIN-ը այս ապրանքագրից չէ', group_no_pack: 'Տուփի քանակը սահմանված չէ',
        units_mismatch: 'Տուփի քանակը չի համընկնում', unknown_scan: 'Անհայտ սկան', no_photo: 'Լուսանկար չկա',
    };
    const flagText = (f) => (f || []).map(x => FLAG[x] || x).join(', ');
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';

    // ---------- Сервер ----------
    function announce(text) { $('crStatus').textContent = text; }
    function showError(text) {
        const box = $('crError');
        if (!text) { box.classList.add('d-none'); return; }
        $('crErrorText').textContent = text;
        box.classList.remove('d-none');
    }
    async function api(url, opts = {}) {
        const init = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
        if (opts.json !== undefined) {
            init.method = 'POST';
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.json);
        } else if (opts.form) {
            init.method = 'POST';
            init.headers['X-Requested-With'] = 'fetch';
            init.body = opts.form;
        }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new Error('Սերվերը հասանելի չէ'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const msg = body && body.error ? body.error
                : resp.status === 401 ? 'Անհրաժեշտ է մուտք գործել' : resp.status === 403 ? 'Միայն ադմինիստրատորի համար' : 'Սերվերի սխալ (' + resp.status + ')';
            throw new Error(msg);
        }
        return body;
    }

    // ---------- Вкладки ----------
    const TABS = ['drivers', 'today', 'money', 'marks', 'settings'];
    const loaded = new Set();
    function showTab(name) {
        if (!TABS.includes(name)) name = 'drivers';
        TABS.forEach(t => {
            $('crPane-' + t).hidden = t !== name;
            $('crTab-' + t).setAttribute('aria-selected', String(t === name));
        });
        showError('');
        if (!loaded.has(name)) { loaded.add(name); LOADERS[name](); }
    }

    // ---------- Водители и терминалы ----------
    const drv = { data: null };
    async function loadDrivers() {
        try {
            drv.data = await api('/api/courier/admin/drivers');
            renderDrivers();
        } catch (e) { showError(e.message); }
    }
    function renderDrivers() {
        const d = drv.data;
        $('crDriverRows').innerHTML = d.drivers.length ? d.drivers.map(x => '<tr class="' + (x.active ? '' : 'is-closed') + '">'
            + '<td>' + esc(x.name) + '</td><td>' + (x.has_pin ? badge('կա', 'b-ok') : badge('չկա', 'b-warn')) + '</td>'
            + '<td>' + (x.active ? 'Աշխատում է' : 'Չի աշխատում') + '</td>'
            + '<td><button type="button" class="rt-linkbtn" data-edit="' + x.id + '">Փոփոխել</button></td></tr>').join('')
            : '<tr><td colspan="4" class="cr-muted">Վարորդներ դեռ չկան</td></tr>';
        const carName = (code) => { const c = d.cars.find(x => x.code === code); return c && c.name ? code + ' · ' + c.name : code; };
        $('crTermRows').innerHTML = d.terminals.length ? d.terminals.map(t => {
            const state = t.revoked_at ? badge('Անջատված', 'b-none')
                : (t.locked_until && new Date(t.locked_until) > new Date()) ? badge('Արգելափակված PIN-ով', 'b-warn') : badge('Աշխատում է', 'b-ok');
            return '<tr class="' + (t.revoked_at ? 'is-closed' : '') + '"><td>' + esc(t.name) + '</td><td>' + esc(carName(t.car_code)) + '</td>'
                + '<td>' + esc(dateTime(t.last_seen_at)) + '</td><td>' + state + '</td>'
                + '<td>' + (t.revoked_at ? '' : '<button type="button" class="rt-linkbtn" data-revoke="' + t.id + '">Անջատել</button>') + '</td></tr>';
        }).join('') : '<tr><td colspan="5" class="cr-muted">Տերմինալներ դեռ չկան</td></tr>';
        const sel = $('crTermCar');
        sel.innerHTML = '<option value="">— ընտրեք —</option>' + d.cars.map(c => '<option value="' + esc(c.code) + '">'
            + esc(c.code + (c.name ? ' · ' + c.name : '') + (c.docs ? ' (' + c.docs + ' ապրանքագիր)' : '')) + '</option>').join('');
        $('crTermCarHint').textContent = d.cars_erp_failed ? 'ERP-ն հասանելի չէ՝ մեքենաների ցուցակը չբեռնվեց։' : 'Մեքենաները, որոնք վերջին 90 օրում առաքել են ERP-ի ապրանքագրերով։';
    }
    function editDriver(id) {
        const x = drv.data.drivers.find(v => v.id === id);
        if (!x) return;
        $('crDriverId').value = String(x.id);
        $('crDriverName').value = x.name;
        $('crDriverPin').value = '';
        $('crDriverActive').checked = x.active;
        $('crDriverFormTitle').textContent = 'Փոփոխել՝ ' + x.name;
        $('crDriverPinHint').textContent = x.has_pin ? '(դատարկ՝ չփոխել)' : '(4–6 թվանշան)';
        $('crDriverNew').hidden = false;
        $('crDriverName').focus();
    }
    function resetDriverForm() {
        $('crDriverForm').reset();
        $('crDriverId').value = '';
        $('crDriverFormTitle').textContent = 'Նոր վարորդ';
        $('crDriverPinHint').textContent = '(4–6 թվանշան)';
        $('crDriverNew').hidden = true;
        $('crDriverErr').textContent = '';
    }
    async function saveDriver(ev) {
        ev.preventDefault();
        const id = $('crDriverId').value ? Number($('crDriverId').value) : null;
        const pin = $('crDriverPin').value.trim();
        if (pin && !/^\d{4,6}$/.test(pin)) { $('crDriverErr').textContent = 'PIN-ը 4–6 թվանշան է'; return; }
        if (id === null && !pin) { $('crDriverErr').textContent = 'Նոր վարորդի համար գրեք PIN'; return; }
        try {
            await api('/api/courier/admin/drivers', { json: { id, name: $('crDriverName').value, pin: pin || null, active: $('crDriverActive').checked } });
            resetDriverForm();
            announce('Վարորդը պահպանված է');
            await loadDrivers();
        } catch (e) { $('crDriverErr').textContent = e.message; }
    }
    async function createTerminal(ev) {
        ev.preventDefault();
        $('crTermErr').textContent = '';
        try {
            const r = await api('/api/courier/admin/terminals', { json: { name: $('crTermName').value, car_code: $('crTermCar').value, url: $('crTermUrl').value } });
            $('crQr').innerHTML = r.qr_svg || '<p style="color:#000;padding:8px">QR-ը չստեղծվեց (segno գրադարանը չկա) — օգտագործեք տեքստը ներքևում</p>';
            $('crQrText').textContent = r.qr_text;
            $('crQrBox').hidden = false;
            $('crTermForm').reset();
            announce('Տերմինալը ստեղծված է — սկանավորեք QR-ը');
            await loadDrivers();
        } catch (e) { $('crTermErr').textContent = e.message; }
    }
    async function revokeTerminal(id) {
        const t = drv.data.terminals.find(v => v.id === id);
        if (!t || !window.confirm('Անջատե՞լ «' + t.name + '» տերմինալը։ Այն այլևս չի կարողանա միանալ, պետք կլինի նոր QR։')) return;
        try {
            await api('/api/courier/admin/terminals/' + id + '/revoke', { json: {} });
            announce('Տերմինալն անջատված է');
            await loadDrivers();
        } catch (e) { showError(e.message); }
    }

    // ---------- Доставки сегодня ----------
    async function loadToday() {
        const box = $('crTodayCars');
        box.innerHTML = '<div class="rt-loading"><div class="rt-spinner" aria-hidden="true"></div>Բեռնում եմ…</div>';
        try {
            const d = await api('/api/courier/admin/today?date=' + encodeURIComponent($('crTodayDate').value || today()));
            renderToday(d);
        } catch (e) { box.innerHTML = ''; showError(e.message); }
    }
    function renderToday(d) {
        const mm = d.mismatch || {};
        $('crMismatch').innerHTML = (mm.items && mm.items.length)
            ? '<div class="rt-alert is-warn"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="rt-alert-text"><b>ERP-ում ապրանքագիրը այլ մեքենայի վրա է, քան «Առաքում» պլանում</b><ul>'
              + mm.items.map(x => '<li>' + esc(x.customer_name || x.customer_code) + ' · ' + esc(x.doc_number) + ' — ERP՝ ' + esc(x.erp_car || 'առանց մեքենայի')
              + ', պլան՝ ' + esc(x.plan_cars.join(', ')) + '</li>').join('') + '</ul></span></div>'
            : (mm.error ? '<p class="cr-lead">Համեմատել պլանի հետ չհաջողվեց՝ ' + esc(mm.error) + '</p>' : '');
        $('crTodayCars').innerHTML = d.cars.length ? d.cars.map(carCard).join('')
            : '<p class="rt-empty">Այս օրվա համար տվյալներ չկան։ Տերմինալ ունեցող մեքենաների կետերը կերևան այստեղ։</p>';
        $('crFlaggedBox').hidden = !d.flagged.length;
        $('crFlaggedNote').textContent = d.flagged.length ? String(d.flagged.length) : '';
        $('crFlaggedRows').innerHTML = d.flagged.map(f => '<tr><td>' + esc(timeOf(f.at)) + '</td><td>' + esc(f.car_code) + '</td><td>' + esc(f.driver_name || '')
            + '</td><td>' + esc(TYPE[f.type] || f.type) + '</td><td>' + esc(flagText(f.flags)) + '</td></tr>').join('');
        $('crRejectedBox').hidden = !d.rejected.length;
        $('crRejectedNote').textContent = d.rejected.length ? String(d.rejected.length) : '';
        $('crRejectedRows').innerHTML = d.rejected.map(r => '<li><span class="grow">' + esc(r.driver_name || '') + ' · ' + esc(TYPE[r.type] || r.type || '')
            + ' — ' + esc(r.message) + '</span><span class="cr-muted">' + esc(dateTime(r.received_at)) + '</span></li>').join('');
    }
    function carCard(c) {
        const stat = (v, label, cls) => '<div class="cr-stat ' + (v ? cls : '') + '"><b>' + fmt(v) + '</b><span>' + esc(label) + '</span></div>';
        const done = c.full + c.partial + c.refuse;
        return '<section class="rt-card"><div class="rt-card-head"><h2 class="rt-card-title"><i class="fas fa-truck" aria-hidden="true"></i>' + esc(c.car_code) + '</h2>'
            + '<span class="rt-card-state ' + (c.total && done === c.total ? 'is-ok' : 'is-todo') + '">' + fmt(done) + ' / ' + fmt(c.total) + ' կետ</span></div>'
            + (c.error ? '<p class="rt-ferr">' + esc(c.error) + '</p>' : '')
            + '<div class="cr-stats">' + stat(c.full, 'Ստացված է', 'is-ok') + stat(c.partial, 'Մասնակի', 'is-warn') + stat(c.refuse, 'Հրաժարում', 'is-bad')
            + stat(c.pending, 'Սպասում է', '') + stat(c.unreadable, 'Կոդը չի կարդացվում', 'is-warn') + stat(c.foreign, 'Այլ մեքենայի կետ', 'is-warn')
            + stat(c.flagged, 'Ուշադրություն', 'is-bad') + '</div>'
            + '<div class="cr-meta"><span>Վարորդ՝ ' + esc(c.drivers.join(', ') || '—') + '</span><span>Վերջին կապը՝ ' + esc(dateTime(c.last_contact)) + '</span>'
            + (c.removed.length ? '<span>Հանված կետեր՝ ' + fmt(c.removed.length) + '</span>' : '') + '</div>'
            + (c.stops.length ? '<details class="rt-fold" style="margin-top:10px"><summary><span class="rt-fold-t">Կետերը</span></summary><div class="rt-fold-body"><div class="rt-table-scroll"><table class="rt-table cr-small">'
              + '<thead><tr><th scope="col">№</th><th scope="col">Հաճախորդ</th><th scope="col">Ապրանքագիր</th><th scope="col">Վճարում</th><th scope="col">Գումար</th><th scope="col">Վիճակ</th></tr></thead><tbody>'
              + c.stops.map(s => '<tr><td>' + fmt(s.seq) + '</td><td>' + esc(s.customer) + '</td><td>' + esc(s.doc_number) + '</td><td>' + esc(COLLECT[s.collect] || '')
                + '</td><td class="cr-num-cell">' + money(s.amount_due) + '</td><td>' + badge(...(STATUS[s.status] || [s.status, 'b-none'])) + '</td></tr>').join('')
              + '</tbody></table></div></div></details>' : '')
            + '</section>';
    }

    // ---------- Деньги ----------
    async function loadMoney() {
        try {
            const d = await api('/api/courier/admin/money?date=' + encodeURIComponent($('crMoneyDate').value || today()));
            renderMoney(d);
        } catch (e) { showError(e.message); }
    }
    function renderMoney(d) {
        const list = $('crMoneyList');
        if (!d.drivers.length) { list.innerHTML = '<p class="rt-empty">Այս օրը վարորդները գումար չեն վերցրել։</p>'; return; }
        list.innerHTML = d.drivers.map(x => {
            const diff = x.diff === null ? '' : '<span class="cr-diff ' + (Math.abs(x.diff) < 0.005 ? 'is-ok">Համընկնում է' : 'is-bad">Տարբերություն՝ ' + money(x.diff)) + '</span>';
            return '<section class="rt-card" data-driver="' + x.driver_id + '"><div class="rt-card-head"><h2 class="rt-card-title"><i class="fas fa-user" aria-hidden="true"></i>' + esc(x.name) + '</h2></div>'
                + '<div class="rt-table-scroll"><table class="rt-table cr-small"><thead><tr><th scope="col">Ապրանքագիր</th><th scope="col">Հաճախորդ</th><th scope="col">Վճարում</th>'
                + '<th scope="col">Պետք է</th><th scope="col">Ապրանքագրով</th><th scope="col">Պարտքի դիմաց</th><th scope="col">ՀԴՄ կտրոն</th><th scope="col">Նշում</th></tr></thead><tbody>'
                + x.rows.map(r => '<tr><td>' + esc(r.doc_number || r.stop_id) + '</td><td>' + esc(r.customer || '') + '</td><td>' + esc(COLLECT[r.collect] || '') + '</td>'
                    + '<td class="cr-num-cell">' + (r.amount_due === null || r.amount_due === undefined ? '—' : money(r.amount_due)) + '</td>'
                    + '<td class="cr-num-cell">' + money(r.invoice) + '</td><td class="cr-num-cell">' + money(r.debt) + '</td>'
                    + '<td>' + esc(r.receipts.join(', ')) + '</td><td>' + esc(flagText(r.flags)) + '</td></tr>').join('')
                + '</tbody></table></div>'
                + '<div class="cr-money-foot"><span class="cr-total">Ապրանքագրերով՝ <b>' + money(x.collected_invoice) + '</b></span>'
                + '<span class="cr-total">Պարտքի դիմաց՝ <b>' + money(x.collected_debt) + '</b></span>'
                + '<span class="cr-total">Ընդամենը հանձնելու՝ <b>' + money(x.collected) + '</b></span>'
                + '<label for="crHand' + x.driver_id + '">Հանձնել է փաստացի</label>'
                + '<input id="crHand' + x.driver_id + '" class="rt-input" inputmode="decimal" value="' + (x.handed === null ? '' : esc(x.handed)) + '">'
                + '<button type="button" class="rt-btn rt-btn-primary rt-btn-sm" data-hand="' + x.driver_id + '">Պահպանել</button>' + diff
                + (x.handed_by ? '<span class="cr-muted">' + esc(x.handed_by) + ', ' + esc(dateTime(x.handed_at)) + '</span>' : '') + '</div></section>';
        }).join('');
    }
    async function saveHandover(driverId) {
        const raw = $('crHand' + driverId).value.replace(/\s/g, '').replace(',', '.');
        const handed = raw === '' ? null : Number(raw);
        if (handed !== null && (!Number.isFinite(handed) || handed < 0)) { showError('Գրեք գումարը թվով'); return; }
        try {
            await api('/api/courier/admin/money/handover', { json: { date: $('crMoneyDate').value || today(), driver_id: driverId, handed } });
            announce('Պահպանված է');
            await loadMoney();
        } catch (e) { showError(e.message); }
    }

    // ---------- Маркировка ----------
    const marks = { rows: [] };
    function marksQuery() {
        const p = new URLSearchParams();
        if ($('crMarksQ').value.trim()) p.set('q', $('crMarksQ').value.trim());
        if ($('crMarksFrom').value) p.set('from', $('crMarksFrom').value);
        if ($('crMarksTo').value) p.set('to', $('crMarksTo').value);
        return p.toString();
    }
    async function loadMarks(ev) {
        if (ev) ev.preventDefault();
        $('crMarksCsv').href = '/api/courier/admin/marks.csv?' + marksQuery();
        try {
            const d = await api('/api/courier/admin/marks?' + marksQuery());
            marks.rows = d.rows;
            $('crMarksNote').textContent = d.rows.length ? 'Գտնվել է՝ ' + fmt(d.rows.length) + (d.rows.length >= d.limit ? ' (ցույց է տրված առաջին ' + fmt(d.limit) + '-ը)' : '') : 'Ոչինչ չի գտնվել';
            $('crMarksRows').innerHTML = d.rows.map(r => '<tr class="' + (r.cancelled ? 'is-closed' : '') + '"><td>' + esc(dateTime(r.at)) + '</td>'
                + '<td class="cr-mono">' + esc((r.raw || '').replace(/\u001d/g, '<GS>')) + (r.duplicate_elsewhere ? ' ' + badge('կրկնված', 'b-danger') : '') + (r.cancelled ? ' ' + badge('չեղարկված', 'b-none') : '') + '</td>'
                + '<td class="cr-mono">' + esc(r.gtin || '') + '<br>' + esc(r.serial || '') + '</td><td>' + esc(r.product_name || '') + '</td>'
                + '<td>' + esc(r.customer_name || '') + (r.tax_id ? '<br><span class="cr-muted">ՀՎՀՀ ' + esc(r.tax_id) + '</span>' : '') + '</td>'
                + '<td>' + esc(r.doc_number || '') + '</td><td>' + esc(r.driver_name || '') + '<br><span class="cr-muted">' + esc(r.car_code) + '</span></td>'
                + '<td>' + (r.kind === 'return' ? 'վերադարձ' : 'վաճառք') + (r.is_group ? ', տուփ (' + fmt(r.units) + ')' : '') + '</td></tr>').join('');
        } catch (e) { showError(e.message); }
    }
    function exportMarksExcel() {
        if (typeof window.XLSX === 'undefined') { showError('Excel-ի գրադարանը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ) — օգտագործեք CSV'); return; }
        const head = ['Կոդ', 'GTIN', 'Սերիական համար', 'Ապրանքի կոդ', 'Ապրանք', 'Հաճախորդի կոդ', 'Հաճախորդ', 'ՀՎՀՀ', 'Ապրանքագիր', 'Ամսաթիվ', 'Ժամանակ',
            'Վարորդ', 'Մեքենա', 'Տեսակ', 'Խմբային', 'Հատ', 'Կրկնված այլ տեղ', 'Չեղարկված'];
        const rows = marks.rows.map(r => [(r.raw || '').replace(/\u001d/g, '<GS>'), r.gtin, r.serial, r.product_code, r.product_name, r.customer_code, r.customer_name,
            r.tax_id, r.doc_number, r.date, r.at, r.driver_name, r.car_code, r.kind === 'return' ? 'վերադարձ' : 'վաճառք', r.is_group ? 'այո' : '', r.units,
            r.duplicate_elsewhere ? 'այո' : '', r.cancelled ? 'այո' : '']);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([head, ...rows]), 'Մակնշում');
        XLSX.writeFile(wb, 'makanshum_' + ($('crMarksFrom').value || 'all') + '_' + ($('crMarksTo').value || 'all') + '.xlsx');
        announce('Excel ֆայլը ներբեռնված է');
    }

    // ---------- Настройки ----------
    const set = { data: null, changed: new Map() };
    async function loadSettings() {
        try {
            set.data = await api('/api/courier/admin/settings');
            set.changed.clear();
            renderSettings();
        } catch (e) { showError(e.message); }
    }
    function productState(p) { return set.changed.get(p.id) || { marked: p.marked, pack_qty: p.pack_qty }; }
    function renderProducts() {
        const d = set.data;
        const q = $('crProdQ').value.trim().toLowerCase();
        const onlySold = $('crProdSold').checked, onlyMarked = $('crProdMarked').checked;
        const rows = d.products.filter(p => !p.container && (!onlySold || p.sold_90) && (!onlyMarked || productState(p).marked)
            && (!q || p.code.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)));
        $('crProdErr').textContent = d.products_erp_failed ? 'ERP-ն հասանելի չէ՝ ապրանքների ցուցակը չբեռնվեց։' : '';
        $('crProdRows').innerHTML = rows.length ? rows.map(p => {
            const s = productState(p);
            return '<tr class="' + (p.closed ? 'is-closed' : '') + '"><td class="cr-mono">' + esc(p.code) + '</td><td>' + esc(p.name)
                + (p.marked_erp ? ' ' + badge('ERP', 'b-erp') : '') + '</td><td class="cr-num-cell">' + (p.sold_90 ? fmt(p.sold_qty_90) : '—') + '</td>'
                + '<td><input type="checkbox" data-mark="' + p.id + '"' + (s.marked ? ' checked' : '') + ' aria-label="Մակնշվող՝ ' + esc(p.name) + '"></td>'
                + '<td><input class="rt-input cr-num" data-pack="' + p.id + '" inputmode="numeric" value="' + (s.pack_qty === null ? '' : esc(s.pack_qty)) + '"'
                + ' placeholder="' + (p.pack_qty_erp ? esc(p.pack_qty_erp) : '') + '" aria-label="Հատ տուփում՝ ' + esc(p.name) + '"></td></tr>';
        }).join('') : '<tr><td colspan="5" class="cr-muted">Ապրանքներ չկան</td></tr>';
        $('crProdSave').disabled = !set.changed.size;
    }
    function reasonItem(kind, r) {
        return '<li class="' + (r.active ? '' : 'is-off') + '"><span class="grow">' + esc(r.text) + '</span>'
            + '<button type="button" class="rt-linkbtn" data-reason="' + esc(kind) + '" data-rid="' + esc(r.id) + '" data-text="' + esc(r.text) + '" data-active="' + (r.active ? '0' : '1') + '">'
            + (r.active ? 'Թաքցնել' : 'Վերադարձնել') + '</button></li>';
    }
    function renderSettings() {
        const d = set.data;
        renderProducts();
        $('crTareList').innerHTML = d.tare_custom.length ? d.tare_custom.map(t => '<li class="' + (t.active ? '' : 'is-off') + '"><span class="grow">' + esc(t.name) + '</span>'
            + '<button type="button" class="rt-linkbtn" data-tare="' + t.id + '" data-name="' + esc(t.name) + '" data-active="' + (t.active ? '0' : '1') + '">'
            + (t.active ? 'Թաքցնել' : 'Վերադարձնել') + '</button></li>').join('') : '<li class="cr-muted">Լրացուցիչ տարա չկա</li>';
        $('crReasonRefuse').innerHTML = d.reasons.refuse.map(r => reasonItem('refuse', r)).join('');
        $('crReasonReturn').innerHTML = d.reasons.return.map(r => reasonItem('return', r)).join('');
        $('crApkCurrent').textContent = d.release
            ? 'Հիմա տերմինալներին տրվում է՝ ' + d.release.version_name + ' (version_code ' + d.release.version_code + ', ' + fmt(d.release.size / 1048576, 1) + ' ՄԲ, ' + dateTime(d.release.uploaded_at) + ')։ Նորը բեռնելիս version_code-ը պետք է մեծ լինի։'
            : 'APK դեռ բեռնված չէ։ Տերմինալները ստուգում են թարմացումը մուտք գործելիս։';
    }
    function onProductInput(ev) {
        const t = ev.target;
        const id = Number(t.dataset.mark || t.dataset.pack);
        if (!id) return;
        const p = set.data.products.find(x => x.id === id);
        const s = { ...productState(p) };
        if (t.dataset.mark) s.marked = t.checked;
        else {
            const raw = t.value.trim();
            s.pack_qty = raw === '' ? null : Number(raw.replace(',', '.'));
        }
        set.changed.set(id, s);
        $('crProdSave').disabled = false;
    }
    async function saveProducts() {
        const items = [...set.changed.entries()].map(([id, s]) => ({ product_id: id, marked: s.marked, pack_qty: s.pack_qty }));
        if (items.some(i => i.pack_qty !== null && (!Number.isFinite(i.pack_qty) || i.pack_qty < 1))) { $('crProdErr').textContent = 'Տուփում հատերի քանակը պետք է լինի 1 կամ ավելի'; return; }
        try {
            await api('/api/courier/admin/settings/products', { json: { items } });
            announce('Պահպանված է');
            await loadSettings();
        } catch (e) { $('crProdErr').textContent = e.message; }
    }
    async function postAndReload(url, json) {
        try { await api(url, { json }); announce('Պահպանված է'); await loadSettings(); } catch (e) { showError(e.message); }
    }
    async function uploadApk(ev) {
        ev.preventDefault();
        $('crApkErr').textContent = '';
        const f = $('crApkFile').files[0];
        if (!f) return;
        const form = new FormData();
        form.append('file', f);
        form.append('version_code', $('crApkCode').value.trim());
        form.append('version_name', $('crApkName').value.trim());
        try {
            await api('/api/courier/admin/apk', { form });
            $('crApkForm').reset();
            announce('APK-ն բեռնված է');
            await loadSettings();
        } catch (e) { $('crApkErr').textContent = e.message; }
    }

    const LOADERS = { drivers: loadDrivers, today: loadToday, money: loadMoney, marks: loadMarks, settings: loadSettings };

    // ---------- События ----------
    function init() {
        ['crTodayDate', 'crMoneyDate'].forEach(id => { $(id).value = today(); });
        $('crDriverForm').addEventListener('submit', saveDriver);
        $('crDriverNew').addEventListener('click', resetDriverForm);
        $('crTermForm').addEventListener('submit', createTerminal);
        $('crQrHide').addEventListener('click', () => { $('crQrBox').hidden = true; $('crQr').innerHTML = ''; $('crQrText').textContent = ''; });
        $('crPane-drivers').addEventListener('click', (ev) => {
            const b = ev.target.closest('button');
            if (!b) return;
            if (b.dataset.edit) editDriver(Number(b.dataset.edit));
            if (b.dataset.revoke) revokeTerminal(Number(b.dataset.revoke));
        });
        $('crTodayRefresh').addEventListener('click', loadToday);
        $('crTodayDate').addEventListener('change', loadToday);
        $('crMoneyRefresh').addEventListener('click', loadMoney);
        $('crMoneyDate').addEventListener('change', loadMoney);
        $('crMoneyList').addEventListener('click', (ev) => { const b = ev.target.closest('[data-hand]'); if (b) saveHandover(Number(b.dataset.hand)); });
        $('crMarksForm').addEventListener('submit', loadMarks);
        $('crMarksXlsx').addEventListener('click', exportMarksExcel);
        ['crProdQ', 'crProdSold', 'crProdMarked'].forEach(id => $(id).addEventListener('input', renderProducts));
        $('crProdRows').addEventListener('change', onProductInput);
        $('crProdSave').addEventListener('click', saveProducts);
        $('crTareForm').addEventListener('submit', (ev) => { ev.preventDefault(); postAndReload('/api/courier/admin/settings/tare', { name: $('crTareName').value, active: true }); });
        $('crTareList').addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-tare]');
            if (b) postAndReload('/api/courier/admin/settings/tare', { id: Number(b.dataset.tare), name: b.dataset.name, active: b.dataset.active === '1' });
        });
        document.querySelectorAll('form.cr-inline[data-kind]').forEach(f => f.addEventListener('submit', (ev) => {
            ev.preventDefault();
            postAndReload('/api/courier/admin/settings/reasons', { kind: f.dataset.kind, text: f.querySelector('input').value, active: true });
        }));
        ['crReasonRefuse', 'crReasonReturn'].forEach(id => $(id).addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-reason]');
            if (b) postAndReload('/api/courier/admin/settings/reasons', { kind: b.dataset.reason, id: b.dataset.rid, text: b.dataset.text, active: b.dataset.active === '1' });
        }));
        $('crApkForm').addEventListener('submit', uploadApk);
        window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
        showTab(location.hash.slice(1));
    }
    document.addEventListener('DOMContentLoaded', init);
})();

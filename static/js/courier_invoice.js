/* «Ապրանքագիր» /courier/invoice — одна накладная глазами офиса: деньги, доставка, сканы маркировки, фото и подпись.
   Поиск: GET /api/courier/admin/invoices?q=&from=&to= (courier/views.invoice_search);
   карточка: GET /api/courier/admin/invoice?date=&stop= (courier/views.invoice_card).
   Адрес: ?q=&from=&to= — список, ?date=&stop= — карточка (ссылки с /courier и /courier/money).
   Всё, что пришло с сервера, выводится только через esc(); фото — только /api/courier/admin/photos/<uuid>. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const money = (v) => (num(v) === null ? '—' : fmt(v, 2) + ' ֏');
    const EPS = 0.005;
    const sum = (list, f) => list.reduce((a, x) => a + (num(f(x)) || 0), 0);
    const iso = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const today = () => iso(new Date());
    const hm = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(11, 16) : '—');
    const dmy = (ds) => (typeof ds === 'string' && ds.length >= 10 ? ds.slice(8, 10) + '.' + ds.slice(5, 7) + '.' + ds.slice(0, 4) : '—');
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    const WEEKDAY = ['կիրակի', 'երկուշաբթի', 'երեքշաբթի', 'չորեքշաբթի', 'հինգշաբթի', 'ուրբաթ', 'շաբաթ'];
    const MONTH = ['հունվարի', 'փետրվարի', 'մարտի', 'ապրիլի', 'մայիսի', 'հունիսի', 'հուլիսի', 'օգոստոսի', 'սեպտեմբերի',
        'հոկտեմբերի', 'նոյեմբերի', 'դեկտեմբերի'];
    function dayName(ds) {
        const [y, m, d] = ds.split('-').map(Number);
        return d + ' ' + MONTH[m - 1] + ' ' + y + ', ' + WEEKDAY[new Date(y, m - 1, d).getDay()];
    }

    // Վիճակը և նշումները՝ ինչպես /courier-ում և /courier/money-ում (պայմանագիր §5 կետ 12)
    const STATUS = { full: ['Ստացված է', 'b-ok'], partial: ['Մասնակի', 'b-warn'], refused: ['Հրաժարում', 'b-danger'], pending: ['Սպասում է', 'b-none'],
        in_progress: ['Ընթացքում', 'b-warn'], covered: ['Պատվերով արված է', 'b-ok'] };
    const COLLECT = { cash: 'Կանխիկ', cash_ecr: 'Կանխիկ ՀԴՄ', none: 'Չվերցնել', ask: 'Ճշտել' };
    const CASH = ['cash', 'cash_ecr'];
    const TYPE = { delivery: 'Առաքում', payment: 'Վճարում', tare: 'Տարա', return: 'Վերադարձ', scan: 'Սկան',
        unreadable: 'Կոդը չի կարդացվում', arrived: 'Ժամանում', geo_suggest: 'Կետի առաջարկ' };
    const ICON = { delivery: 'fa-box', payment: 'fa-coins', tare: 'fa-wine-bottle', return: 'fa-rotate-left', scan: 'fa-barcode',
        unreadable: 'fa-ban', arrived: 'fa-location-dot', geo_suggest: 'fa-map-pin' };
    const FLAG = {
        foreign: 'Այլ մեքենայի կամ օրվա կետ', unknown_stop: 'Անհայտ կետ', duplicate_elsewhere: 'Կոդն արդեն տրվել է այլ տեղ',
        repeat: 'Կրկնակի սկան', scan_short: 'Մակնշման սկանը պակաս է', no_ecr_receipt: 'ՀԴՄ կտրոնի համարը չկա',
        paid_collect_none: 'Գումար է վերցվել, թեև պետք չէր', lines_incomplete: 'Ոչ բոլոր տողերն են նշված',
        unknown_line: 'Անհայտ տող', gtin_not_in_invoice: 'GTIN-ը այս ապրանքագրից չէ', group_no_pack: 'Տուփի քանակը սահմանված չէ',
        units_mismatch: 'Տուփի քանակը չի համընկնում', unknown_scan: 'Անհայտ սկան', no_photo: 'Լուսանկար չկա',
        qty_over_invoice: 'Քանակը ավելի է, քան ապրանքագրի վերջին տարբերակում', no_reason: 'Պատճառը նշված չէ',
        date_suspicious: 'Ամսաթիվը չի համընկնում ժամանակի հետ', no_payment: 'Վճարում չկա',
        collected_by_other: 'Վերցրել է այլ վարորդ', split_order: 'Մասնակի է՝ բաժանված պատվեր',
        merge_conflict: 'Ստուգել՝ պատվերով և ապրանքագրով նշումները չեն համընկնում',
        helper_unconfirmed: 'Առաքիչը PIN-ով հաստատված չէ',
    };
    const BAD_FLAGS = ['no_payment', 'merge_conflict', 'paid_collect_none', 'scan_short', 'duplicate_elsewhere'];
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
    const statusBadge = (r) => (r.status ? badge(...(STATUS[r.status] || [r.status, 'b-none'])) : '')
        + (r.removed ? ' ' + badge('Հանված է', 'b-none') : '');
    const flagText = (f) => (f || []).map(x => FLAG[x] || x).join(', ');
    const cardUrl = (date, stop) => '/courier/invoice?date=' + encodeURIComponent(date) + '&stop=' + encodeURIComponent(stop);
    const raw = (s) => esc(String(s ?? '').replace(/\x1d/g, '⟨GS⟩'));   // GS маркировки невидим — показываем явно

    // depth — сколько карточек открыто подряд из списка (хранится в history.state): «Արդյունքներ» = history.go(-depth)
    const st = { seq: 0, depth: 0 };

    // ---------- Сервер ----------
    function announce(text) { $('ciStatus').textContent = text; }
    function showError(text) {
        const box = $('ciError');
        if (!text) { box.classList.add('d-none'); return; }
        $('ciErrorText').textContent = text;
        box.classList.remove('d-none');
    }
    async function api(url) {
        let resp, body = null;
        try { resp = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }); } catch (e) { throw new Error('Սերվերը հասանելի չէ'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            throw new Error(body && body.error ? body.error
                : resp.status === 401 ? 'Անհրաժեշտ է մուտք գործել' : resp.status === 403 ? 'Միայն ադմինիստրատորի համար' : 'Սերվերի սխալ (' + resp.status + ')');
        }
        return body;
    }
    const loading = () => '<div class="rt-loading"><div class="rt-spinner"></div>Բեռնվում է…</div>';

    // ---------- Адрес → экран ----------
    function route() {
        const p = new URLSearchParams(location.search);
        st.depth = (history.state && Number.isInteger(history.state.depth)) ? history.state.depth : 0;
        if (p.get('date') && p.get('stop')) {
            openCard(p.get('date'), p.get('stop'));
            return;
        }
        $('ciQ').value = p.get('q') || '';
        if (p.has('from') || p.has('to') || p.has('q')) {
            $('ciFrom').value = p.get('from') || '';
            $('ciTo').value = p.get('to') || '';
        } else {   // по умолчанию — накладные сегодняшнего дня
            $('ciFrom').value = today();
            $('ciTo').value = today();
        }
        search();
    }
    function listUrl() {
        const p = new URLSearchParams();
        if ($('ciQ').value.trim()) p.set('q', $('ciQ').value.trim());
        p.set('from', $('ciFrom').value);
        p.set('to', $('ciTo').value);
        return p.toString();
    }

    // ---------- Список ----------
    async function search() {
        const seq = ++st.seq, q = listUrl();
        $('ciCard').innerHTML = '';
        $('ciPrint').hidden = true;
        document.title = 'Ապրանքագիր — Sales Dashboard';
        if (!$('ciQ').value.trim() && !($('ciFrom').value && $('ciTo').value)) {
            showError('');
            $('ciResults').innerHTML = empty('fa-magnifying-glass', 'Գրեք ապրանքագրի համարը', 'Կամ ընտրեք օրերը՝ դրանց բոլոր ապրանքագրերը տեսնելու համար։');
            return;
        }
        $('ciResults').innerHTML = loading();
        try {
            const d = await api('/api/courier/admin/invoices?' + q);
            if (seq !== st.seq) return;
            showError('');
            $('ciResults').innerHTML = resultsView(d);
            announce('Գտնվել է ' + d.rows.length);
        } catch (e) {
            if (seq !== st.seq) return;
            showError(e.message);
            $('ciResults').innerHTML = '';
        }
    }
    function empty(icon, title, text) {
        return '<div class="ci-empty"><i class="fas ' + icon + '" aria-hidden="true"></i><b>' + esc(title) + '</b><span>' + esc(text) + '</span></div>';
    }
    function resultsView(d) {
        if (!d.rows.length) return empty('fa-file-circle-question', 'Ոչինչ չի գտնվել',
            'Տերմինալը տեսնում է միայն այն ապրանքագրերը, որոնք եղել են մեքենաների օրվա ցուցակում։ Ստուգեք համարը կամ ընդլայնեք օրերը։');
        const rows = d.rows.map(r => '<tr><td class="ci-mono">' + esc(dmy(r.date)) + '</td>'
            + '<td class="ci-mono"><a class="ci-link" href="' + esc(cardUrl(r.date, r.stop_id)) + '">' + esc(r.doc_number || r.stop_id) + '</a>'
            + (r.source === 'order' ? '<small>պատվեր</small>' : '') + (r.found_as ? '<small>գտնվել է ըստ ' + esc(r.found_as) + '</small>' : '') + '</td>'
            + '<td>' + esc(r.customer || '—') + (r.customer_code ? '<small>' + esc(r.customer_code) + '</small>' : '') + '</td>'
            + '<td class="ci-mono">' + esc(r.car_code || '') + '</td><td>' + esc(COLLECT[r.collect] || '—') + '</td>'
            + '<td>' + (statusBadge(r) || '<span class="ci-mute">—</span>') + '</td>'
            + '<td class="ci-n">' + esc(money(r.amount_due)) + '</td><td class="ci-n">' + esc(money(r.paid)) + '</td></tr>').join('');
        return '<p class="ci-found">Գտնվել է <b>' + fmt(d.rows.length) + '</b>'
            + (d.more ? ' (ցույց են տրված միայն վերջին ' + fmt(d.days) + ' օրը, մինչև ' + fmt(d.limit) + ' տող՝ ճշտեք որոնումը կամ օրերը)' : '') + '</p>'
            + '<div class="ci-scroll"><table class="ci-table"><thead><tr><th scope="col">Օր</th><th scope="col">Ապրանքագիր</th><th scope="col">Հաճախորդ</th>'
            + '<th scope="col">Մեքենա</th><th scope="col">Վճարում</th><th scope="col">Վիճակ</th><th scope="col" class="ci-n">Արժե</th><th scope="col" class="ci-n">Վճարված</th></tr></thead>'
            + '<tbody>' + rows + '</tbody></table></div>';
    }

    // ---------- Карточка ----------
    async function openCard(date, stop) {
        const seq = ++st.seq;
        $('ciResults').innerHTML = '';
        $('ciCard').innerHTML = loading();
        $('ciPrint').hidden = true;
        try {
            const c = await api('/api/courier/admin/invoice?date=' + encodeURIComponent(date) + '&stop=' + encodeURIComponent(stop));
            if (seq !== st.seq) return;
            showError('');
            $('ciCard').innerHTML = cardView(c);
            $('ciPrint').hidden = false;
            document.title = 'Ապրանքագիր ' + (c.stop.doc_number || '') + ' — Sales Dashboard';
            announce('Ապրանքագիր ' + (c.stop.doc_number || ''));
        } catch (e) {
            if (seq !== st.seq) return;
            showError(e.message);
            $('ciCard').innerHTML = '';
        }
    }

    function moneyFigures(c) {
        const s = c.stop, rows = c.money;
        const expected = rows.some(r => num(r.expected) !== null) ? sum(rows, r => r.expected) : null;
        const shortRow = rows.find(r => num(r.short) !== null);
        return { expected, short: shortRow ? shortRow.short : null, debt: sum(c.payments.filter(p => p.kind === 'debt' && !p.is_cancel && !p.cancelled), p => p.amount),   // как events.payments_total
            receipts: [...new Set(rows.flatMap(r => r.receipts || []))], cash: CASH.includes(s.collect) };
    }
    function scanFigures(c) {
        const marked = c.lines.filter(ln => ln.marked);
        const need = sum(marked, ln => (num(ln.delivered) !== null ? ln.delivered : ln.qty));
        return { marked: marked.length, need, got: sum(marked, ln => ln.scanned) };
    }

    function cardView(c) {
        const s = c.stop, m = moneyFigures(c), sc = scanFigures(c);
        const cust = s.customer || {};
        const back = st.depth > 0 ? '<button type="button" class="ci-backlist" id="ciBackList"><i class="fas fa-arrow-left" aria-hidden="true"></i>Արդյունքներ</button>' : '';
        const notes = [];
        if (c.opened_as) notes.push('Բացել եք ' + esc(c.opened_as) + '-ը՝ այն փոխարինվել է այս ապրանքագրով, նրա նշումները ցույց են տրված այստեղ։');
        if (c.related.paid_to) notes.push('Այս ապրանքագրի վճարումները հաշվվում են <a class="ci-link" href="' + esc(cardUrl(c.date, c.related.paid_to.stop_id)) + '">' + esc(c.related.paid_to.doc_number || 'այլ ապրանքագրում') + '</a>-ում (նույն պատվերը)։');
        if (c.related.split.length) notes.push('Պատվերը բաժանված է՝ նաև ' + c.related.split.map(x => '<a class="ci-link" href="' + esc(cardUrl(c.date, x.stop_id)) + '">' + esc(x.doc_number || x.stop_id) + '</a>').join(', ') + '։');
        if (c.related.absorbed.length) notes.push('Ներառում է ' + c.related.absorbed.map(x => (x.source === 'order' ? 'պատվեր ' : 'նախկին ապրանքագիր ') + esc(x.doc_number || x.stop_id)).join(', ') + '։');
        const flags = new Set([...s.flags, ...c.money.flatMap(r => r.flags || []), ...c.timeline.flatMap(t => t.flags)]);
        const chips = [...flags].map(f => badge(FLAG[f] || f, BAD_FLAGS.includes(f) ? 'b-danger' : 'b-warn')).join('');
        const who = [c.drivers.length ? '<span><i class="fas fa-user" aria-hidden="true"></i>' + esc(c.drivers.join(', ')) + '</span>' : '',
            c.helpers.length ? '<span><i class="fas fa-user-group" aria-hidden="true"></i>Առաքիչ՝ ' + esc(c.helpers.join(', ')) + '</span>' : ''].join('');
        return back
            + '<article class="ci-card">'
            + '<header class="ci-card-head"><div class="ci-title-row"><h2 class="ci-doc"><span>' + (s.source === 'order' ? 'Պատվեր' : 'Ապրանքագիր') + '</span>' + esc(s.doc_number || s.stop_id) + '</h2>'
            + '<span class="ci-status">' + (statusBadge(s) || badge('Առաքում չկա', 'b-none')) + '</span></div>'
            + '<p class="ci-meta"><span><i class="fas fa-calendar-day" aria-hidden="true"></i>' + esc(dayName(c.date)) + '</span>'
            + (s.car_code ? '<span class="ci-car">' + esc(s.car_code) + '</span>' : '')
            + (s.seq ? '<span>Կետ № ' + esc(s.seq) + '</span>' : '')
            + '<span><i class="fas fa-wallet" aria-hidden="true"></i>' + esc(COLLECT[s.collect] || 'Վճարման ձևը հայտնի չէ') + '</span>'
            + (s.agent_name ? '<span><i class="fas fa-user-tie" aria-hidden="true"></i>' + esc(s.agent_name) + '</span>' : '') + who + '</p>'
            + '<div class="ci-cust"><b>' + esc(cust.name || '—') + '</b><span>'
            + [cust.code ? 'Կոդ՝ ' + esc(cust.code) : '', cust.tax_id ? 'ՀՎՀՀ՝ ' + esc(cust.tax_id) : '', cust.address ? esc(cust.address) : '', cust.phone ? esc(cust.phone) : ''].filter(Boolean).join(' · ')
            + '</span></div>'
            + (notes.length ? '<div class="ci-notes">' + notes.map(n => '<p><i class="fas fa-link" aria-hidden="true"></i>' + n + '</p>').join('') + '</div>' : '')
            + (chips ? '<div class="ci-chips">' + chips + '</div>' : '')
            + '</header>'
            + kpis(c, m, sc)
            + section('fa-coins', 'Գումար', c.payments.length ? fmt(c.payments.length) + ' գրառում' : '', paymentsView(c, m))
            + section('fa-boxes-stacked', 'Ապրանքներ', fmt(c.lines.length) + ' տող', linesView(c) + statementsView(c))
            + section('fa-barcode', 'Մակնշման սկաններ', c.scans.length ? fmt(c.scans.length) : '', scansView(c, sc))
            + section('fa-camera', 'Լուսանկարներ և ստորագրություն', photoCount(c) ? fmt(photoCount(c)) : '', photosView(c))
            + (c.tare.expected.length || c.tare.marked.length ? section('fa-wine-bottle', 'Տարա', '', tareView(c)) : '')
            + section('fa-clock-rotate-left', 'Ժամանակագրություն', c.timeline.length ? fmt(c.timeline.length) + ' նշում' : '', timelineView(c))
            + '</article>';
    }

    function kpis(c, m, sc) {
        const s = c.stop;
        const dueSub = num(s.due) !== null && Math.abs((s.due || 0) - (s.amount_due || 0)) > EPS ? 'Ըստ առաքվածի՝ <b>' + esc(money(s.due)) + '</b>' : 'Ապրանքագրի գումարը';
        let exp, expSub;
        if (!m.cash) {
            exp = COLLECT[s.collect] || '—';
            expSub = s.collect === 'none' ? 'Վարորդը գումար չի վերցնում' : 'Վճարման ձևը ճշտել';
        } else if (m.expected === null) {
            exp = '—';
            expSub = c.related.paid_to ? 'Հաշվվում է այլ ապրանքագրում' : 'Առաքումը դեռ նշված չէ';
        } else {
            exp = money(m.expected);
            expSub = s.status === 'in_progress' ? 'Դեռ վերջնական չէ' : 'Կանխիկ՝ առաքվածի դիմաց';
        }
        const paid = num(s.paid) || 0;
        let paidSub = m.receipts.length ? 'ՀԴՄ կտրոն՝ <b>' + esc(m.receipts.join(', ')) + '</b>' : (s.collect === 'cash_ecr' && paid > EPS ? '<span class="ci-bad">ՀԴՄ կտրոն չկա</span>' : 'Ապրանքագրով');
        if (m.debt > EPS) paidSub += '<span class="ci-kpi-line">Պարտքի դիմաց՝ <b>' + esc(money(m.debt)) + '</b></span>';
        let paidCls = '';
        if (m.cash && m.short !== null) paidCls = m.short > EPS ? ' is-bad' : m.short < -EPS ? ' is-over' : ' is-ok';
        const shortLine = m.cash && m.short !== null ? (m.short > EPS ? 'Պակաս՝ <b>' + esc(money(m.short)) + '</b>' : m.short < -EPS ? 'Ավել՝ <b>' + esc(money(-m.short)) + '</b>' : 'Ամբողջը վերցված է') : '';
        const scanVal = sc.marked ? fmt(sc.got, 3) + ' / ' + fmt(sc.need, 3) : '—';
        const scanCls = !sc.marked ? '' : sc.got + EPS >= sc.need ? ' is-ok' : ' is-bad';
        const scanSub = sc.marked ? fmt(c.scans.filter(x => !x.cancelled).length) + ' սկան'
            + (c.unreadable > EPS ? ' · ' + fmt(c.unreadable, 3) + ' չի կարդացվում' : '') + ' · ' + fmt(sc.marked) + ' մակնշվող տող' : 'Մակնշվող ապրանք չկա';
        return '<section class="ci-kpis" aria-label="Ամփոփում">'
            + kpi('fa-file-invoice', 'Արժե', money(s.amount_due), dueSub)
            + kpi('fa-hand-holding-dollar', 'Պետք էր վերցնել', exp, expSub)
            + kpi('fa-sack-dollar', 'Վերցրել է', money(paid), paidSub + (shortLine ? '<span class="ci-kpi-line">' + shortLine + '</span>' : ''), paidCls)
            + kpi('fa-barcode', 'Մակնշում', scanVal, scanSub, scanCls)
            + '</section>';
    }
    function kpi(icon, label, value, sub, cls = '') {
        return '<div class="ci-kpi' + cls + '"><span class="ci-kpi-l"><i class="fas ' + icon + '" aria-hidden="true"></i>' + esc(label) + '</span>'
            + '<b class="ci-kpi-v">' + esc(value) + '</b><span class="ci-kpi-s">' + sub + '</span></div>';
    }
    function section(icon, title, note, body) {
        return '<section class="ci-sec"><h3 class="ci-sec-t"><i class="fas ' + icon + '" aria-hidden="true"></i>' + esc(title)
            + (note ? '<span class="ci-sec-n">' + esc(note) + '</span>' : '') + '</h3>' + body + '</section>';
    }
    const none = (text) => '<p class="ci-none">' + esc(text) + '</p>';

    function paymentsView(c, m) {
        const rows = c.payments.map(p => {
            const state = p.is_cancel ? badge('Չեղարկում', 'b-none') : p.cancelled ? badge('Չեղարկված', 'b-none') : '';
            const note = [state, p.doc_number ? '<span class="ci-mute">' + esc(p.doc_number) + '-ով</span>' : '',
                p.flags.length ? '<span class="ci-mute">' + esc(flagText(p.flags)) + '</span>' : ''].filter(Boolean).join(' ');
            return '<tr class="' + (p.is_cancel || p.cancelled ? 'is-muted' : '') + '"><td class="ci-mono">' + esc(hm(p.at)) + '</td>'
                + '<td>' + esc(p.driver_name || '—') + (p.helper_name ? '<small>Առաքիչ՝ ' + esc(p.helper_name) + '</small>' : '') + '</td>'
                + '<td>' + (p.kind === 'debt' ? 'Պարտքի դիմաց' : 'Ապրանքագրով') + '</td>'
                + '<td class="ci-n">' + (p.is_cancel ? '−' : '') + esc(money(p.amount)) + '</td>'
                + '<td class="ci-mono">' + esc(p.receipt || '—') + '</td><td>' + (note || '') + '</td></tr>';
        }).join('');
        const lines = c.money.length > 1 ? '<ul class="ci-drv-money">' + c.money.map(r => '<li><b>' + esc(r.name) + '</b>՝ պետք էր '
            + esc(money(r.expected)) + ', վերցրել է ' + esc(money(r.invoice)) + (r.debt > EPS ? ' + պարտք ' + esc(money(r.debt)) : '') + '</li>').join('') + '</ul>' : '';
        const warn = m.cash && m.expected > EPS && !c.payments.some(p => p.kind === 'invoice' && !p.is_cancel && !p.cancelled)
            ? '<div class="rt-alert is-warn ci-alert"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="rt-alert-text">Վարորդը պետք է վերցներ <b>' + esc(money(m.expected)) + '</b>, բայց տերմինալում վճարում նշված չէ։</span></div>' : '';
        if (!c.payments.length) return warn + none(m.cash ? 'Վճարումներ չկան։' : 'Վճարումներ չկան՝ այս ապրանքագրով վարորդը գումար չի վերցնում։');
        return warn + lines + '<div class="ci-scroll"><table class="ci-table"><thead><tr><th scope="col">Ժամ</th><th scope="col">Ով</th><th scope="col">Տեսակ</th>'
            + '<th scope="col" class="ci-n">Գումար</th><th scope="col">ՀԴՄ կտրոն</th><th scope="col">Նշում</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    }

    function linesView(c) {
        if (!c.lines.length) return none('Տողեր չկան։');
        const hasDelivered = c.lines.some(ln => num(ln.delivered) !== null);
        const anyMarked = c.lines.some(ln => ln.marked);
        const rows = c.lines.map((ln, i) => {
            const short = hasDelivered && (num(ln.delivered) || 0) + EPS < (num(ln.qty) || 0);
            const need = num(ln.delivered) !== null ? ln.delivered : ln.qty;
            const mark = !ln.marked ? '<span class="ci-mute">—</span>'
                : (num(ln.scanned) || 0) + EPS >= (num(need) || 0) ? badge(fmt(ln.scanned, 3) + ' / ' + fmt(need, 3), 'b-ok')
                    : badge(fmt(ln.scanned, 3) + ' / ' + fmt(need, 3), 'b-danger');
            return '<tr class="' + (short ? 'is-warn' : '') + '"><td class="ci-i">' + (i + 1) + '</td><td class="ci-mono">' + esc(ln.code || '') + '</td>'
                + '<td>' + esc(ln.name || '—') + '</td><td class="ci-n">' + esc(fmt(ln.qty, 3)) + ' <small>' + esc(ln.unit || '') + '</small></td>'
                + (hasDelivered ? '<td class="ci-n' + (short ? ' ci-bad' : '') + '">' + esc(fmt(ln.delivered, 3)) + '</td>' : '')
                + '<td class="ci-n">' + esc(money(ln.price)) + '</td><td class="ci-n">' + esc(money(ln.sum)) + '</td>'
                + (anyMarked ? '<td>' + mark + '</td>' : '') + '</tr>';
        }).join('');
        const span = 3 + (hasDelivered ? 2 : 1) + 1;
        return '<div class="ci-scroll"><table class="ci-table"><thead><tr><th scope="col">#</th><th scope="col">Կոդ</th><th scope="col">Ապրանք</th>'
            + '<th scope="col" class="ci-n">Ապրանքագրում</th>' + (hasDelivered ? '<th scope="col" class="ci-n">Առաքված</th>' : '')
            + '<th scope="col" class="ci-n">Գին</th><th scope="col" class="ci-n">Գումար</th>' + (anyMarked ? '<th scope="col">Մակնշում</th>' : '') + '</tr></thead>'
            + '<tbody>' + rows + '</tbody><tfoot><tr><th scope="row" colspan="' + span + '">Ընդամենը</th><td class="ci-n">' + esc(money(sum(c.lines, ln => ln.sum))) + '</td>'
            + (anyMarked ? '<td></td>' : '') + '</tr></tfoot></table></div>'
            + (!hasDelivered && !c.statements.length ? '<p class="ci-hint">Առաքումը դեռ նշված չէ՝ «Առաքված» սյունակը կհայտնվի, երբ վարորդը նշի։</p>' : '')
            + (c.stop.lines_changed ? '<p class="ci-hint"><i class="fas fa-circle-info" aria-hidden="true"></i> Ապրանքագիրը փոխվել է առաքումից հետո՝ ցույց են տրված այն տողերը, որոնցով վարորդը նշել է առաքումը։</p>' : '');
    }
    function statementsView(c) {
        return c.statements.map(x => '<div class="ci-stmt"><p class="ci-stmt-t"><i class="fas fa-box" aria-hidden="true"></i>Առաքումը նշվել է '
            + (x.source === 'order' ? 'պատվեր ' : 'նախկին ապրանքագիր ') + esc(x.doc_number || '') + '-ով՝ ' + esc(hm(x.at)) + (x.driver_name ? ', ' + esc(x.driver_name) : '') + '</p>'
            + '<div class="ci-scroll"><table class="ci-table"><thead><tr><th scope="col">Կոդ</th><th scope="col">Ապրանք</th><th scope="col" class="ci-n">Փաստաթղթում</th><th scope="col" class="ci-n">Առաքված</th></tr></thead><tbody>'
            + x.lines.map(ln => '<tr><td class="ci-mono">' + esc(ln.code || '') + '</td><td>' + esc(ln.name || '—') + '</td><td class="ci-n">' + esc(fmt(ln.qty, 3))
                + '</td><td class="ci-n">' + esc(fmt(ln.delivered, 3)) + '</td></tr>').join('') + '</tbody></table></div></div>').join('');
    }

    function scansView(c, sc) {
        if (!c.scans.length) return none(sc.marked ? 'Սկաններ չկան՝ մակնշվող ապրանքը դեռ չի սկանավորվել։' : 'Սկաններ չկան։');
        const rows = c.scans.map(x => {
            const note = [x.cancelled ? badge('Չեղարկված', 'b-none') : '', x.duplicate_elsewhere ? badge('Կոդն արդեն տրվել է այլ տեղ', 'b-danger') : '',
                !x.counted && !x.cancelled ? badge('Չի հաշվվել (կրկնակի)', 'b-warn') : ''].join(' ');
            return '<tr class="' + (x.cancelled ? 'is-muted' : x.duplicate_elsewhere ? 'is-bad' : '') + '"><td class="ci-mono">' + esc(hm(x.at)) + '</td>'
                + '<td class="ci-code">' + raw(x.raw) + '</td><td>' + esc(x.product_name || '—') + '</td>'
                + '<td class="ci-n">' + esc(fmt(x.units, 3)) + (x.is_group ? ' <small>տուփ</small>' : '') + '</td>'
                + '<td>' + (x.kind === 'return' ? 'Վերադարձ' : 'Վաճառք') + '</td><td>' + esc(x.driver_name || '—') + '</td><td>' + note + '</td></tr>';
        }).join('');
        return '<div class="ci-scroll"><table class="ci-table"><thead><tr><th scope="col">Ժամ</th><th scope="col">Կոդ</th><th scope="col">Ապրանք</th>'
            + '<th scope="col" class="ci-n">Հատ</th><th scope="col">Տեսակ</th><th scope="col">Վարորդ</th><th scope="col">Նշում</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    }

    const photoCount = (c) => c.timeline.reduce((a, t) => a + t.photos.filter(p => UUID.test(p.id)).length, 0);
    function photosView(c) {
        const items = c.timeline.flatMap(t => t.photos.filter(p => UUID.test(p.id)).map(p => ({ p, t })))
            .sort((a, b) => (a.p.kind === 'signature' ? 0 : 1) - (b.p.kind === 'signature' ? 0 : 1));
        if (!items.length) {
            const need = c.timeline.some(t => t.flags.includes('no_photo'));
            return none(need ? 'Լուսանկար չկա, թեև պետք էր (մասնակի առաքում, հրաժարում կամ վերադարձ)։' : 'Լուսանկարներ և ստորագրություն չկան։');
        }
        return '<div class="ci-gallery">' + items.map(({ p, t }) => {
            const url = '/api/courier/admin/photos/' + p.id, sig = p.kind === 'signature';
            const label = (sig ? 'Ստորագրություն' : 'Լուսանկար') + ' · ' + (TYPE[t.type] || t.type) + ' · ' + hm(t.at);
            return '<a class="ci-photo' + (sig ? ' is-sig' : '') + '" href="' + url + '" target="_blank" rel="noopener">'
                + '<img src="' + url + '" alt="' + esc(label) + '" loading="lazy"><span>' + esc(label) + '</span></a>';
        }).join('') + '</div>';
    }

    function tareView(c) {
        const list = (items) => items.map(t => esc(t.name || t.tare_id) + '՝ <b>' + esc(fmt(t.qty, 2)) + '</b>').join(', ') || '—';
        return '<dl class="ci-dl"><dt>Ըստ ապրանքագրի</dt><dd>' + list(c.tare.expected) + '</dd><dt>Նշել է վարորդը</dt><dd>' + list(c.tare.marked) + '</dd></dl>';
    }

    function eventText(t) {
        const i = t.info || {};
        switch (t.type) {
            case 'delivery': return [i.status ? (STATUS[i.status] || [i.status])[0] : '', i.actual ? '' : 'փոխարինված է հաջորդով',
                i.reason ? 'պատճառ՝ ' + i.reason : '', i.comment ? '«' + i.comment + '»' : ''].filter(Boolean).map(esc).join(' · ');
            case 'payment': return esc((i.is_cancel ? 'չեղարկում ' : '') + (i.kind === 'debt' ? 'պարտքի դիմաց ' : '') + money(i.amount)
                + (i.receipt ? ' · ՀԴՄ ' + i.receipt : ''));
            case 'scan': return raw(i.raw) + esc(' · ' + fmt(i.units, 3) + (i.kind === 'return' ? ' · վերադարձ' : ''));
            case 'return': return esc([i.product, fmt(i.qty, 3), i.reason, i.comment ? '«' + i.comment + '»' : ''].filter(Boolean).join(' · '));
            default: return esc(i.comment ? '«' + i.comment + '»' : '');
        }
    }
    function timelineView(c) {
        if (!c.timeline.length) return none('Տերմինալից նշումներ դեռ չկան։');
        return '<ol class="ci-tl">' + c.timeline.map(t => {
            const txt = eventText(t);
            const thumbs = t.photos.filter(p => UUID.test(p.id)).map(p => '<a class="ci-thumb" href="/api/courier/admin/photos/' + p.id
                + '" target="_blank" rel="noopener"><img src="/api/courier/admin/photos/' + p.id + '" alt="' + (p.kind === 'signature' ? 'Ստորագրություն' : 'Լուսանկար') + '" loading="lazy" width="44" height="44"></a>').join('');
            return '<li class="ci-tl-i' + (t.flags.length ? ' has-flag' : '') + '"><span class="ci-tl-time">' + esc(hm(t.at)) + '</span>'
                + '<span class="ci-tl-dot" aria-hidden="true"><i class="fas ' + (ICON[t.type] || 'fa-circle') + '"></i></span>'
                + '<div class="ci-tl-body"><b>' + esc(TYPE[t.type] || t.type) + '</b>' + (txt ? '<span class="ci-tl-txt">' + txt + '</span>' : '')
                + '<span class="ci-tl-who">' + esc(t.driver_name || '') + (t.helper_name ? ' · առաքիչ ' + esc(t.helper_name) : '') + (t.doc_number ? ' · ' + esc(t.doc_number) + '-ով' : '') + '</span>'
                + (t.flags.length ? '<span class="ci-tl-flags">' + esc(flagText(t.flags)) + '</span>' : '')
                + (thumbs ? '<span class="ci-tl-ph">' + thumbs + '</span>' : '') + '</div></li>';
        }).join('') + '</ol>';
    }

    // ---------- События ----------
    $('ciForm').addEventListener('submit', (ev) => {
        ev.preventDefault();
        st.depth = 0;
        history.pushState({ depth: 0 }, '', '/courier/invoice?' + listUrl());
        search();
    });
    document.addEventListener('click', (ev) => {
        const a = ev.target.closest('a.ci-link');
        if (a && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey && ev.button === 0) {
            const u = new URL(a.href, location.href);
            if (u.pathname === '/courier/invoice' && u.searchParams.get('stop')) {
                ev.preventDefault();
                st.depth = $('ciResults').innerHTML ? 1 : st.depth > 0 ? st.depth + 1 : 0;   // из списка — 1, из карточки списка — +1
                history.pushState({ depth: st.depth }, '', u.pathname + u.search);
                openCard(u.searchParams.get('date'), u.searchParams.get('stop'));
                window.scrollTo(0, 0);
            }
            return;
        }
        if (ev.target.closest('#ciBackList') && st.depth > 0) history.go(-st.depth);
    });
    window.addEventListener('popstate', route);
    $('ciPrint').addEventListener('click', () => window.print());
    route();
})();

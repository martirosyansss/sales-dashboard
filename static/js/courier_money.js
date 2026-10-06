/* «Վարորդների գումարը» /courier/money — касса: деньги водителей за день (раньше вкладка «Գումար» на /courier).
   Данные: GET /api/courier/admin/money?date= (courier/views.money_view), «сдал фактически» —
   POST /api/courier/admin/money/handover {date, driver_id, handed|null, comment}. handed null снимает отметку.
   Сводка дня, карточка на водителя (свёрнута; с проблемами — раскрыта), акт сдачи для печати, Excel (SheetJS из base_v2).
   Всё, что пришло с сервера, выводится только через esc(). */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const money = (v) => (num(v) === null ? '—' : fmt(v, 2) + ' ֏');
    const EPS = 0.005;
    const zero = (v) => Math.abs(v || 0) < EPS;
    const signed = (v) => (zero(v) ? '' : v > 0 ? '+' : '−') + money(zero(v) ? 0 : Math.abs(v));
    const sum = (list, f) => list.reduce((a, x) => a + (num(f(x)) || 0), 0);
    const iso = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const today = () => iso(new Date());
    const dateTime = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(8, 10) + '.' + s.slice(5, 7) + ' ' + s.slice(11, 16) : '—');

    const WEEKDAY = ['Կիրակի', 'Երկուշաբթի', 'Երեքշաբթի', 'Չորեքշաբթի', 'Հինգշաբթի', 'Ուրբաթ', 'Շաբաթ'];
    const MONTH = ['հունվարի', 'փետրվարի', 'մարտի', 'ապրիլի', 'մայիսի', 'հունիսի', 'հուլիսի', 'օգոստոսի', 'սեպտեմբերի',
        'հոկտեմբերի', 'նոյեմբերի', 'դեկտեմբերի'];
    function dayName(ds) {
        const [y, m, d] = ds.split('-').map(Number);
        const wd = new Date(y, m - 1, d).getDay();
        return y + ' թ. ' + MONTH[m - 1] + ' ' + d + ', ' + WEEKDAY[wd].toLowerCase();
    }

    // Վիճակը և նշումները՝ ինչպես /courier-ում (courier.js, պայմանագիր §5 կետ 12)
    const STATUS = { full: ['Ստացված է', 'b-ok'], partial: ['Մասնակի', 'b-warn'], refused: ['Հրաժարում', 'b-danger'], pending: ['Սպասում է', 'b-none'],
        in_progress: ['Ընթացքում', 'b-warn'], covered: ['Պատվերով արված է', 'b-ok'] };
    const COLLECT = { cash: 'Կանխիկ', cash_ecr: 'Կանխիկ ՀԴՄ', none: 'Չվերցնել', ask: 'Ճշտել' };
    const FLAG = {
        foreign: 'Այլ մեքենայի կամ օրվա կետ', unknown_stop: 'Անհայտ կետ', no_ecr_receipt: 'ՀԴՄ կտրոնի համարը չկա',
        paid_collect_none: 'Գումար է վերցվել, թեև պետք չէր', lines_incomplete: 'Ոչ բոլոր տողերն են նշված',
        qty_over_invoice: 'Քանակը ավելի է, քան ապրանքագրի վերջին տարբերակում', no_reason: 'Պատճառը նշված չէ',
        date_suspicious: 'Ամսաթիվը չի համընկնում ժամանակի հետ', no_payment: 'Վճարում չկա',
        collected_by_other: 'Վերցրել է այլ վարորդ', split_order: 'Մասնակի է՝ բաժանված պատվեր',
        merge_conflict: 'Ստուգել՝ պատվերով և ապրանքագրով նշումները չեն համընկնում',
        helper_unconfirmed: 'Առաքիչը PIN-ով հաստատված չէ', repeat: 'Կրկնակի', unknown_line: 'Անհայտ տող',
    };
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
    const statusBadge = (r) => (r.status ? badge(...(STATUS[r.status] || [r.status, 'b-none'])) : '<span class="cm-mute">—</span>')
        + (r.removed ? ' ' + badge('Հանված է', 'b-none') : '');

    const st = { data: null, filter: 'all', q: '', open: new Set(), dirty: new Map(), seenDate: null, busy: false, seq: 0 };

    // ---------- Сервер ----------
    function announce(text) { $('cmStatus').textContent = text; }
    function showError(text) {
        const box = $('cmError');
        if (!text) { box.classList.add('d-none'); return; }
        $('cmErrorText').textContent = text;
        box.classList.remove('d-none');
    }
    async function api(url, json) {
        const init = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
        if (json !== undefined) {
            init.method = 'POST';
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(json);
        }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new Error('Սերվերը հասանելի չէ'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            throw new Error(body && body.error ? body.error
                : resp.status === 401 ? 'Անհրաժեշտ է մուտք գործել' : resp.status === 403 ? 'Միայն ադմինիստրատորի համար' : 'Սերվերի սխալ (' + resp.status + ')');
        }
        return body;
    }

    // ---------- Модель ----------
    const day = () => $('cmDate').value || today();
    const cars = (x) => [...new Set(x.rows.map(r => r.car_code).filter(Boolean))];
    const conflicts = (x) => x.rows.filter(r => r.flags.includes('merge_conflict')).length;
    const unpaid = (x) => sum(x.rows, r => (r.short > EPS ? r.short : 0));   // переплата одной точки не гасит недобор другой
    const hasIssue = (x) => x.no_payment > 0 || unpaid(x) > 0 || conflicts(x) > 0 || (x.diff !== null && !zero(x.diff));
    function verdict(x) {
        if (x.handed === null) return { cls: 'is-open', icon: 'fa-hourglass-half', text: 'Չի հանձնել' };
        if (zero(x.diff)) return { cls: 'is-ok', icon: 'fa-circle-check', text: 'Համընկնում է' };
        return x.diff < 0 ? { cls: 'is-bad', icon: 'fa-circle-minus', text: 'Պակաս ' + money(-x.diff) }
            : { cls: 'is-over', icon: 'fa-circle-plus', text: 'Ավել ' + money(x.diff) };
    }
    function matches(x) {
        if (st.filter === 'issues' && !hasIssue(x)) return false;
        if (st.filter === 'open' && x.handed !== null) return false;
        if (!st.q) return true;
        const q = st.q.toLowerCase();
        return x.name.toLowerCase().includes(q) || cars(x).some(c => c.toLowerCase().includes(q))
            || x.rows.some(r => [r.customer, r.doc_number].some(s => s && String(s).toLowerCase().includes(q)));
    }

    // ---------- Загрузка ----------
    async function load() {
        const ds = day(), seq = ++st.seq;
        st.busy = true;
        $('cmRefresh').classList.add('is-busy');
        try {
            const d = await api('/api/courier/admin/money?date=' + encodeURIComponent(ds));
            if (seq !== st.seq || d.date !== day()) return;   // пришёл более новый запрос или сменили дату — ответ устарел
            st.data = d;
            if (st.seenDate !== d.date) {   // новая дата: раскрыть водителей с проблемами
                st.seenDate = d.date;
                st.open = new Set(d.drivers.filter(hasIssue).map(x => x.driver_id));
            }
            showError('');
            render();
            const now = new Date();
            $('cmUpdated').textContent = 'Թարմացված է ' + String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
        } catch (e) {
            if (seq !== st.seq) return;
            showError(e.message);
            if (!st.data) $('cmList').innerHTML = '';
            else document.querySelectorAll('form[data-hand] button').forEach(b => { b.disabled = false; });
        } finally {
            if (seq !== st.seq) return;
            st.busy = false;
            $('cmRefresh').classList.remove('is-busy');
        }
    }
    function setDay(ds) {
        if (st.dirty.size && !window.confirm('Չպահպանված գումարներ կան։ Անցնե՞լ այլ օր առանց պահպանելու։')) {
            $('cmDate').value = st.seenDate || today();
            return;
        }
        st.dirty.clear();
        $('cmDate').value = ds;
        const url = new URL(location.href);
        if (ds === today()) url.searchParams.delete('date'); else url.searchParams.set('date', ds);
        history.replaceState(null, '', url);
        paintDay();
        st.data = null;   // карточки прежнего дня — убрать сразу, чтобы сумма не ушла не в тот день
        $('cmKpis').innerHTML = '';
        $('cmAttention').innerHTML = '';
        $('cmList').innerHTML = '<div class="rt-loading"><div class="rt-spinner"></div>Բեռնվում է…</div>';
        load();
    }
    function shiftDay(n) {
        const [y, m, d] = day().split('-').map(Number);
        setDay(iso(new Date(y, m - 1, d + n)));
    }
    function paintDay() {
        const ds = day();
        $('cmDayName').innerHTML = esc(dayName(ds)) + (ds === today() ? ' ' + badge('Այսօր', 'b-gps') : '');
        $('cmNext').disabled = ds >= today();
        $('cmToday').hidden = ds === today();
    }

    // ---------- Отрисовка ----------
    function render() {
        const list = st.data.drivers;
        renderKpis(list);
        renderAttention(list);
        $('cmNAll').textContent = list.length || '';
        $('cmNIssues').textContent = list.filter(hasIssue).length || '';
        $('cmNOpen').textContent = list.filter(x => x.handed === null).length || '';
        const shown = list.filter(matches);
        const box = $('cmList');
        if (!list.length) {
            box.innerHTML = '<div class="cm-empty"><i class="fas fa-wallet" aria-hidden="true"></i><b>Այս օրը վարորդները գումար չեն վերցրել</b>'
                + '<span>Գումարը երևում է, երբ վարորդը տերմինալում նշում է վճարում կամ կանխիկ վճարման կետ է առաքում։</span></div>';
        } else if (!shown.length) {
            box.innerHTML = '<div class="cm-empty"><i class="fas fa-filter" aria-hidden="true"></i><b>Ոչինչ չի գտնվել</b><span>Փոխեք ֆիլտրը կամ որոնումը։</span></div>';
        } else {
            box.innerHTML = shown.map(driverCard).join('');
            st.dirty.forEach((v, id) => {   // չպահպանված թվերը՝ վերադարձնել
                const inp = $('cmHand' + id), com = $('cmCom' + id);
                if (inp) { inp.value = v.handed; inp.closest('form').classList.add('is-dirty'); }
                if (com) com.value = v.comment;
            });
        }
        const allOpen = shown.length > 0 && shown.every(x => st.open.has(x.driver_id));
        $('cmExpand').querySelector('span').textContent = allOpen ? 'Փակել բոլորը' : 'Բացել բոլորը';
        $('cmExpand').querySelector('i').className = 'fas ' + (allOpen ? 'fa-down-left-and-up-right-to-center' : 'fa-up-right-and-down-left-from-center');
        $('cmExpand').disabled = !shown.length;
        renderPrint(list);
    }

    function renderKpis(list) {
        const expected = sum(list, x => x.expected), collected = sum(list, x => x.collected);
        const handed = list.filter(x => x.handed !== null);
        const handedSum = sum(handed, x => x.handed), diff = sum(handed, x => x.diff);
        const waiting = sum(list.filter(x => x.handed === null), x => x.collected);
        const stops = list.reduce((a, x) => a + x.rows.filter(r => (r.expected || 0) > 0).length, 0);
        const pct = list.length ? Math.round(handed.length / list.length * 100) : 0;
        const diffCls = !handed.length ? '' : zero(diff) ? ' is-ok' : diff < 0 ? ' is-bad' : ' is-over';
        $('cmKpis').innerHTML = [
            kpi('fa-file-invoice', 'Պետք էր վերցնել', money(expected), fmt(stops) + ' կետ՝ կանխիկ վճարմամբ'),
            kpi('fa-hand-holding-dollar', 'Վերցրել են', money(collected),
                'Ապրանքագրերով <b>' + esc(money(sum(list, x => x.collected_invoice))) + '</b> · պարտքի դիմաց <b>' + esc(money(sum(list, x => x.collected_debt))) + '</b>'),
            kpi('fa-vault', 'Հանձնված է դրամարկղ', money(handedSum),
                '<span class="cm-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '" aria-label="Հանձնել են"><i style="width:' + pct + '%"></i></span>'
                + '<span>' + fmt(handed.length) + ' / ' + fmt(list.length) + ' վարորդ</span>'),
            kpi('fa-scale-balanced', 'Տարբերություն', handed.length ? signed(diff) : '—',
                !list.length ? 'Տվյալներ չկան' : waiting > EPS ? 'Դեռ սպասվում է՝ <b>' + esc(money(waiting)) + '</b>' : handed.length ? 'Բոլորը հանձնել են' : 'Դեռ ոչ ոք չի հանձնել', diffCls),
        ].join('');
    }
    function kpi(icon, label, value, sub, cls = '') {
        return '<div class="cm-kpi' + cls + '"><span class="cm-kpi-l"><i class="fas ' + icon + '" aria-hidden="true"></i>' + esc(label) + '</span>'
            + '<b class="cm-kpi-v">' + esc(value) + '</b><span class="cm-kpi-s">' + sub + '</span></div>';
    }

    function renderAttention(list) {
        const noPay = sum(list, x => x.no_payment), short = sum(list, unpaid), conf = sum(list, conflicts);
        const bad = list.filter(x => x.handed !== null && x.diff < -EPS);
        const parts = [];
        if (noPay) parts.push('<b>' + fmt(noPay) + '</b> կետում վճարում չկա');
        if (short > EPS) parts.push('չվերցված՝ <b>' + esc(money(short)) + '</b>');
        if (bad.length) parts.push('<b>' + fmt(bad.length) + '</b> վարորդ հանձնել է պակաս');
        if (conf) parts.push('<b>' + fmt(conf) + '</b> կետ՝ ստուգել վարորդի հետ');
        $('cmAttention').innerHTML = parts.length
            ? '<div class="rt-alert is-warn cm-attn"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="rt-alert-text">' + parts.join(' · ') + '</span>'
                + (st.filter !== 'issues' ? '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-filter="issues">Ցույց տալ</button>' : '') + '</div>'
            : '';
    }

    function initials(name) {
        return name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
    }

    function driverCard(x) {
        const id = Number(x.driver_id), open = st.open.has(x.driver_id), v = verdict(x), cs = cars(x);
        const conf = conflicts(x), short = unpaid(x);
        const chips = [];
        if (x.no_payment) chips.push(badge('Վճարում չկա՝ ' + fmt(x.no_payment) + ' կետ', 'b-danger'));
        if (conf) chips.push(badge('Ստուգել՝ ' + fmt(conf) + ' կետ', 'b-warn'));
        const meta = [cs.length ? cs.map(c => '<span class="cm-car">' + esc(c) + '</span>').join('') : '',
            '<span>' + fmt(x.rows.length) + ' կետ</span>'].filter(Boolean).join('');
        const who = x.handed_by ? '<p class="cm-who"><i class="fas fa-user-check" aria-hidden="true"></i>Ընդունել է ' + esc(x.handed_by) + ' · ' + esc(dateTime(x.handed_at))
            + (x.comment ? ' · <q>' + esc(x.comment) + '</q>' : '') + '</p>' : '';
        return '<article class="cm-drv' + (open ? ' is-open' : '') + (hasIssue(x) ? ' has-issue' : '') + '" data-driver="' + id + '">'
            + '<div class="cm-drv-head">'
            + '<button type="button" class="cm-drv-toggle" data-toggle="' + id + '" aria-expanded="' + open + '" aria-controls="cmBody' + id + '">'
            + '<span class="cm-ava" aria-hidden="true">' + esc(initials(x.name)) + '</span>'
            + '<span class="cm-drv-id"><b class="cm-drv-name">' + esc(x.name) + '</b><span class="cm-drv-meta">' + meta + '</span></span>'
            + '<i class="fas fa-chevron-down cm-chev" aria-hidden="true"></i></button>'
            + '<dl class="cm-figs">'
            + fig('Պետք էր', money(x.expected))
            + fig('Վերցրել է', money(x.collected), x.collected_debt > EPS ? 'որից պարտք ' + money(x.collected_debt) : '')
            + fig('Չվերցված', short > 0 ? money(short) : '—', '', short > 0 ? 'is-bad' : 'is-mute')
            + '</dl>'
            + '<form class="cm-hand" data-hand="' + id + '" novalidate>'
            + '<label for="cmHand' + id + '">Հանձնել է</label>'
            + '<span class="cm-money-in"><input id="cmHand' + id + '" class="rt-input" inputmode="decimal" autocomplete="off" placeholder="0" title="Հանձնելու է՝ ' + esc(money(x.collected)) + '" value="' + (x.handed === null ? '' : esc(fmt(x.handed, 2))) + '"><span aria-hidden="true">֏</span></span>'
            + '<button type="submit" class="rt-btn rt-btn-primary rt-btn-sm">Պահպանել</button>'
            + (x.handed !== null ? '<button type="button" class="rt-iconbtn cm-iconbtn cm-rcpt" data-receipt="' + id + '" aria-label="Տպել անդորրագիրը" title="Տպել անդորրագիրը (2 օրինակ՝ վարորդին և դրամարկղին)"><i class="fas fa-receipt" aria-hidden="true"></i></button>' : '')
            + '</form>'
            + '<span class="cm-verdict ' + v.cls + '"><i class="fas ' + v.icon + '" aria-hidden="true"></i>' + esc(v.text) + '</span>'
            + '</div>'
            + (chips.length || who ? '<div class="cm-drv-note">' + chips.join('') + who + '</div>' : '')
            + '<div class="cm-drv-body" id="cmBody' + id + '"' + (open ? '' : ' hidden') + '>' + rowsTable(x)
            + '<div class="cm-comment"><label for="cmCom' + id + '">Մեկնաբանություն</label>'
            + '<input id="cmCom' + id + '" class="rt-input" maxlength="200" data-com="' + id + '" placeholder="օրինակ՝ մնացածը կբերի վաղը" value="' + esc(x.comment || '') + '">'
            + '<span class="cm-hint">Պահպանվում է «Պահպանել» կոճակով՝ գումարի հետ միասին</span></div>'
            + '</div></article>';
    }
    function fig(label, value, sub = '', cls = '') {
        return '<div class="cm-fig ' + cls + '"><dt>' + esc(label) + '</dt><dd>' + esc(value) + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</dd></div>';
    }

    function rowsTable(x) {
        if (!x.rows.length) return '<p class="cm-norows">Կետեր չկան՝ միայն հանձնման նշում։</p>';
        const cell = (v) => '<td class="cm-n">' + esc(money(v)) + '</td>';
        const body = x.rows.map((r, i) => {
            const cls = r.flags.includes('no_payment') ? 'is-bad' : r.flags.includes('merge_conflict') ? 'is-warn' : r.removed ? 'is-muted' : '';
            const short = r.short === null || r.short === undefined ? '<td class="cm-n cm-mute">—</td>'
                : zero(r.short) ? '<td class="cm-n cm-mute">0</td>'
                    : '<td class="cm-n ' + (r.short > 0 ? 'cm-bad' : 'cm-mute') + '">' + esc(money(r.short)) + '</td>';
            const exp = r.expected === null || r.expected === undefined ? '<td class="cm-n cm-mute">—</td>'
                : '<td class="cm-n">' + esc(money(r.expected)) + (r.status === 'in_progress' ? '<small>դեռ վերջնական չէ</small>' : '') + '</td>';
            const other = !zero((r.invoice_all || 0) - r.invoice) ? ' title="Բոլոր վարորդները՝ ' + esc(money(r.invoice_all)) + '"' : '';
            const flags = r.flags.filter(f => f !== 'no_payment').map(f => FLAG[f] || f);
            const due = r.due === null || r.due === undefined ? '<td class="cm-n cm-mute">—</td>'
                : '<td class="cm-n" title="Ապրանքագիր՝ ' + esc(money(r.invoice_amount)) + '">' + esc(money(r.due)) + '</td>';
            return '<tr class="' + cls + '"><td class="cm-i">' + (i + 1) + '</td>'
                + '<td class="cm-doc">' + (r.known ? '<a class="cm-doc-link" href="/courier/invoice?date=' + encodeURIComponent(st.data.date) + '&stop=' + encodeURIComponent(r.stop_id)
                    + '" title="Ապրանքագրի քարտը՝ սկաններ, լուսանկարներ, ստորագրություն">' + esc(r.doc_number || r.stop_id) + '</a>' : '—') + (r.car_code && cars(x).length > 1 ? '<small>' + esc(r.car_code) + '</small>' : '') + '</td>'
                + '<td class="cm-cust">' + esc(r.customer || '—') + '</td>'
                + '<td>' + esc(COLLECT[r.collect] || '—') + '</td><td>' + statusBadge(r) + '</td>'
                + due + exp + '<td class="cm-n"' + other + '>' + esc(money(r.invoice)) + '</td>' + short + cell(r.debt)
                + '<td class="cm-rc">' + esc(r.receipts.join(', ') || '—') + '</td>'
                + '<td class="cm-flags">' + (r.flags.includes('no_payment') ? badge('Վճարում չկա', 'b-danger') : '') + (flags.length ? '<span>' + esc(flags.join(', ')) + '</span>' : '') + '</td></tr>';
        }).join('');
        const shortSum = unpaid(x);   // как «Չվերցված» в шапке карточки
        return '<div class="cm-scroll"><table class="cm-table"><thead><tr><th scope="col">#</th><th scope="col">Ապրանքագիր</th><th scope="col">Հաճախորդ</th>'
            + '<th scope="col">Վճարում</th><th scope="col">Վիճակ</th><th scope="col" class="cm-n">Արժե</th><th scope="col" class="cm-n">Պետք էր</th>'
            + '<th scope="col" class="cm-n">Վերցրել է</th><th scope="col" class="cm-n">Պակաս</th><th scope="col" class="cm-n">Պարտքի դիմաց</th>'
            + '<th scope="col">ՀԴՄ կտրոն</th><th scope="col">Նշում</th></tr></thead><tbody>' + body + '</tbody>'
            + '<tfoot><tr><th scope="row" colspan="6">Ընդամենը</th>' + cell(x.expected) + cell(sum(x.rows, r => r.invoice))
            + '<td class="cm-n' + (shortSum > EPS ? ' cm-bad' : '') + '">' + esc(money(shortSum)) + '</td>' + cell(sum(x.rows, r => r.debt)) + '<td colspan="2"></td></tr></tfoot></table></div>';
    }

    // ---------- Акт сдачи (печать) ----------
    function renderPrint(list) {
        const ds = st.data.date, now = new Date();
        const rows = list.map((x, i) => '<tr><td>' + (i + 1) + '</td><td>' + esc(x.name) + '</td><td>' + esc(cars(x).join(', ')) + '</td>'
            + '<td class="n">' + esc(money(x.expected)) + '</td><td class="n">' + esc(money(x.collected_invoice)) + '</td><td class="n">' + esc(money(x.collected_debt)) + '</td>'
            + '<td class="n"><b>' + esc(money(x.collected)) + '</b></td><td class="n">' + (x.handed === null ? '' : esc(money(x.handed))) + '</td>'
            + '<td class="n">' + (x.handed === null ? '' : esc(signed(x.diff))) + '</td><td class="sig"></td></tr>').join('');
        const handed = list.filter(x => x.handed !== null);
        $('cmPrintSheet').innerHTML = '<h1>Կանխիկի հանձնման ակտ</h1>'
            + '<p class="pm">Ամսաթիվ՝ <b>' + esc(dayName(ds)) + '</b></p>'
            + '<table><thead><tr><th>#</th><th>Վարորդ</th><th>Մեքենա</th><th class="n">Պետք էր վերցնել</th><th class="n">Ապրանքագրերով</th>'
            + '<th class="n">Պարտքի դիմաց</th><th class="n">Հանձնելու է</th><th class="n">Հանձնել է</th><th class="n">Տարբերություն</th><th>Ստորագրություն</th></tr></thead>'
            + '<tbody>' + (rows || '<tr><td colspan="10">Տվյալներ չկան</td></tr>') + '</tbody>'
            + '<tfoot><tr><th colspan="3">Ընդամենը</th><td class="n">' + esc(money(sum(list, x => x.expected))) + '</td><td class="n">' + esc(money(sum(list, x => x.collected_invoice))) + '</td>'
            + '<td class="n">' + esc(money(sum(list, x => x.collected_debt))) + '</td><td class="n"><b>' + esc(money(sum(list, x => x.collected))) + '</b></td>'
            + '<td class="n">' + esc(money(sum(handed, x => x.handed))) + '</td><td class="n">' + (handed.length ? esc(signed(sum(handed, x => x.diff))) : '') + '</td><td></td></tr></tfoot></table>'
            + '<div class="signs"><span>Գանձապահ՝ ____________________</span><span>Ստուգեց՝ ____________________</span></div>'
            + '<p class="pf">Տպված է ' + esc(dateTime(iso(now) + 'T' + now.toTimeString().slice(0, 5))) + ' · Sales Dashboard · Առաքիչ</p>';
    }

    // ---------- Квитанция водителю (A4: экземпляр водителя + экземпляр кассы) ----------
    function receiptCopy(x, copy) {
        const cs = cars(x), cash = x.rows.filter(r => (r.expected || 0) > 0).length;
        const row = (label, value, cls = '') => '<tr class="' + cls + '"><th>' + esc(label) + '</th><td class="n">' + esc(value) + '</td></tr>';
        const diff = zero(x.diff) ? 'Համընկնում է' : signed(x.diff) + ' (' + (x.diff < 0 ? 'պակաս' : 'ավել') + ')';
        return '<div class="rc"><div class="rc-top"><h2>Կանխիկի ընդունման անդորրագիր</h2><span class="rc-copy">' + esc(copy) + '</span></div>'
            + '<p class="rc-no">№ ' + esc(st.data.date.replace(/-/g, '')) + '-' + Number(x.driver_id) + ' · ' + esc(dayName(st.data.date)) + '</p>'
            + '<dl class="rc-who"><dt>Վարորդ</dt><dd>' + esc(x.name) + '</dd><dt>Մեքենա</dt><dd>' + esc(cs.join(', ') || '—') + '</dd>'
            + '<dt>Կետեր</dt><dd>' + fmt(x.rows.length) + ' (կանխիկ վճարմամբ՝ ' + fmt(cash) + ')</dd></dl>'
            + '<table>' + row('Պետք էր վերցնել', money(x.expected)) + row('Վերցրել է ապրանքագրերով', money(x.collected_invoice))
            + row('Վերցրել է պարտքի դիմաց', money(x.collected_debt)) + row('Հանձնելու է', money(x.collected), 'b')
            + row('Հանձնել է', money(x.handed), 'b big') + row('Տարբերություն', diff, 'b')
            + '</table>'
            + (x.comment ? '<p class="rc-com">Մեկնաբանություն՝ ' + esc(x.comment) + '</p>' : '')
            + '<p class="rc-acc">Ընդունել է՝ ' + esc(x.handed_by || '—') + ', ' + esc(dateTime(x.handed_at)) + '</p>'
            + '<div class="rc-signs"><span>Հանձնեց (վարորդ)՝ ____________________</span><span>Ընդունեց (գանձապահ)՝ ____________________</span></div></div>';
    }
    function printReceipt(id) {
        const x = st.data && st.data.drivers.find(d => d.driver_id === id);
        if (!x || x.handed === null) return;
        if (st.dirty.has(id)) { showError('Նախ պահպանեք գումարը, հետո տպեք անդորրագիրը'); return; }
        $('cmReceipt').innerHTML = receiptCopy(x, 'Վարորդի օրինակ') + '<div class="rc-cut" aria-hidden="true">✂ կտրել այստեղ</div>' + receiptCopy(x, 'Դրամարկղի օրինակ');
        document.body.classList.add('cm-printing-receipt');
        window.addEventListener('afterprint', () => document.body.classList.remove('cm-printing-receipt'), { once: true });
        window.print();
    }

    // ---------- Excel ----------
    function exportExcel() {
        if (!st.data) return;
        if (typeof window.XLSX === 'undefined') { showError('Excel-ի գրադարանը չբեռնվեց (cdn.sheetjs.com-ը հասանելի չէ)'); return; }
        const list = st.data.drivers, n = (v) => num(v);
        const drivers = [['Վարորդ', 'Մեքենա', 'Կետեր', 'Պետք էր վերցնել', 'Ապրանքագրերով', 'Պարտքի դիմաց', 'Հանձնելու է', 'Չվերցված', 'Վճարում չկա (կետ)',
            'Հանձնել է', 'Տարբերություն', 'Ընդունել է', 'Երբ', 'Մեկնաբանություն']]
            .concat(list.map(x => [x.name, cars(x).join(', '), x.rows.length, n(x.expected), n(x.collected_invoice), n(x.collected_debt), n(x.collected),
                unpaid(x), x.no_payment, n(x.handed), n(x.diff), x.handed_by || '', x.handed_at || '', x.comment || '']));
        const stops = [['Վարորդ', 'Մեքենա', 'Ապրանքագիր', 'Հաճախորդ', 'Վճարում', 'Վիճակ', 'Ապրանքագրի գումար', 'Արժե', 'Պետք էր', 'Վերցրել է',
            'Բոլոր վարորդները', 'Պակաս', 'Պարտքի դիմաց', 'ՀԴՄ կտրոն', 'Նշում']];
        list.forEach(x => x.rows.forEach(r => stops.push([x.name, r.car_code || '', r.doc_number || r.stop_id, r.customer || '', COLLECT[r.collect] || '',
            (STATUS[r.status] || [r.status || ''])[0] + (r.removed ? ' (հանված)' : ''), n(r.invoice_amount), n(r.due), n(r.expected), n(r.invoice),
            n(r.invoice_all), n(r.short), n(r.debt), r.receipts.join(', '), r.flags.map(f => FLAG[f] || f).join(', ')])));
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(drivers), 'Վարորդներ');
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(stops), 'Կետեր');
        XLSX.writeFile(wb, 'gumar_' + st.data.date + '.xlsx');
        announce('Excel ֆայլը ներբեռնված է');
    }

    // ---------- «Сдал фактически» ----------
    async function saveHandover(form) {
        const id = Number(form.dataset.hand);
        const inp = $('cmHand' + id), com = $('cmCom' + id), btn = form.querySelector('button');
        if (!st.data || btn.disabled) return;
        const raw = inp.value.replace(/[\s ֏]/g, '').replace(',', '.');
        const handed = raw === '' ? null : Number(raw);
        if (handed !== null && (!Number.isFinite(handed) || handed < 0 || handed > 1e9)) {
            inp.classList.add('is-invalid');
            inp.focus();
            showError('Գրեք գումարը թվով (0 կամ ավելի)');
            return;
        }
        const x = st.data.drivers.find(d => d.driver_id === id);
        if (handed === null && (!x || x.handed === null)) {   // нечего снимать: пустая сумма ничего не сохраняет
            inp.classList.add('is-invalid');
            inp.focus();
            showError(com && com.value.trim() ? 'Գրեք գումարը՝ մեկնաբանությունը պահվում է գումարի հետ միասին' : 'Գրեք հանձնված գումարը');
            return;
        }
        if (handed === null && !window.confirm('Հանե՞լ «' + x.name + '»-ի հանձնման նշումը։')) return;
        btn.disabled = true;
        try {
            await api('/api/courier/admin/money/handover', { date: st.data.date, driver_id: id, handed, comment: com ? com.value.trim() || null : null });
            st.dirty.delete(id);
            announce(handed === null ? 'Նշումը հանված է' : 'Պահպանված է');
            await load();
            const card = document.querySelector('.cm-drv[data-driver="' + id + '"]');
            if (card) { card.classList.add('is-saved'); setTimeout(() => card.classList.remove('is-saved'), 1600); }
        } catch (e) {
            showError(e.message);
            btn.disabled = false;
        }
    }
    function markDirty(id) {
        const inp = $('cmHand' + id), com = $('cmCom' + id);
        if (!inp) return;
        inp.classList.remove('is-invalid');
        st.dirty.set(id, { handed: inp.value, comment: com ? com.value : '' });
        inp.closest('form').classList.add('is-dirty');
    }

    function toggle(id, open) {
        const card = document.querySelector('.cm-drv[data-driver="' + id + '"]');
        if (!card) return;
        if (open === undefined) open = !st.open.has(id);
        if (open) st.open.add(id); else st.open.delete(id);
        card.classList.toggle('is-open', open);
        card.querySelector('.cm-drv-toggle').setAttribute('aria-expanded', String(open));
        $('cmBody' + id).hidden = !open;
    }
    function setFilter(f) {
        st.filter = f;
        document.querySelectorAll('.cm-seg [data-filter]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.filter === f)));
        if (st.data) render();
    }

    // ---------- События ----------
    function init() {
        const q = new URLSearchParams(location.search).get('date');
        $('cmDate').value = q && /^\d{4}-\d{2}-\d{2}$/.test(q) ? q : today();
        $('cmDate').max = today();
        paintDay();
        $('cmDate').addEventListener('change', () => { if ($('cmDate').value) setDay($('cmDate').value); });
        $('cmPrev').addEventListener('click', () => shiftDay(-1));
        $('cmNext').addEventListener('click', () => shiftDay(1));
        $('cmToday').addEventListener('click', () => setDay(today()));
        $('cmRefresh').addEventListener('click', () => { if (!st.busy) load(); });
        $('cmPrint').addEventListener('click', () => { document.body.classList.remove('cm-printing-receipt'); window.print(); });
        $('cmExcel').addEventListener('click', exportExcel);
        $('cmQ').addEventListener('input', () => { st.q = $('cmQ').value.trim(); if (st.data) render(); });
        document.addEventListener('click', (ev) => {
            const f = ev.target.closest('[data-filter]');
            if (f) { setFilter(f.dataset.filter); return; }
            const rc = ev.target.closest('[data-receipt]');
            if (rc) { printReceipt(Number(rc.dataset.receipt)); return; }
            const t = ev.target.closest('[data-toggle]');
            if (t) toggle(Number(t.dataset.toggle));
        });
        $('cmExpand').addEventListener('click', () => {
            const shown = st.data ? st.data.drivers.filter(matches) : [];
            const open = !shown.every(x => st.open.has(x.driver_id));
            shown.forEach(x => toggle(x.driver_id, open));
            render();
        });
        $('cmList').addEventListener('submit', (ev) => {
            const form = ev.target.closest('form[data-hand]');
            if (form) { ev.preventDefault(); saveHandover(form); }
        });
        $('cmList').addEventListener('input', (ev) => {
            const t = ev.target;
            if (t.id && t.id.startsWith('cmHand')) markDirty(Number(t.id.slice(6)));
            if (t.dataset.com) markDirty(Number(t.dataset.com));
        });
        $('cmList').addEventListener('keydown', (ev) => {   // Enter в комментарии — как «Պահպանել»
            if (ev.key === 'Enter' && ev.target.dataset.com) {
                ev.preventDefault();
                const form = document.querySelector('form[data-hand="' + ev.target.dataset.com + '"]');
                if (form) saveHandover(form);
            }
        });
        window.addEventListener('beforeunload', (ev) => { if (st.dirty.size) { ev.preventDefault(); ev.returnValue = ''; } });
        load();
    }
    document.addEventListener('DOMContentLoaded', init);
})();

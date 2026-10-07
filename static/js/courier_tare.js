/* «Տարա» /courier/tare — баланс тары магазинов (ответ владельца №87 п. 9, courier/tare.py).
   API: GET /api/courier/admin/tare (строки магазин × вид тары: начальный остаток, ушло, забрано, баланс),
   GET /api/courier/admin/tare/history?customer= (по дням), POST /api/courier/admin/tare/opening
   {customer_id, tare_id, qty|null, as_of}, POST /api/courier/admin/tare/import {rows, apply} (предпросмотр → запись),
   GET /api/courier/admin/tare.csv. CSRF к fetch добавляет base_v2.html. Всё с сервера и из файла — только через esc(). */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }); };
    const iso = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const today = () => iso(new Date());
    const dateHy = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    const PAGE = 400;                 // строк за раз: дальше — «Ցույց տալ ևս»
    const FIELD = { code: 'Խանութի կոդ', tare: 'Տարա', qty: 'Քանակ', as_of: 'Ամսաթիվ' };

    const st = { data: null, shown: PAGE, seq: 0, store: null, preview: null };

    // ---------- Сервер ----------
    function announce(text) { $('ctStatus').textContent = text; }
    function showError(text) {
        const box = $('ctError');
        if (!text) { box.classList.add('d-none'); return; }
        $('ctErrorText').textContent = text;
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
            const err = new Error(body && body.error ? body.error
                : resp.status === 401 ? 'Անհրաժեշտ է մուտք գործել' : resp.status === 403 ? 'Միայն ադմինիստրատորի համար' : 'Սերվերի սխալ (' + resp.status + ')');
            err.body = body;
            throw err;
        }
        return body;
    }

    // ---------- Модель ----------
    const kindName = (t) => { const k = st.data && st.data.kinds.find(x => x.tare_id === t); return k ? k.name : t; };
    const storeText = (r) => (r.name || '—') + (r.code ? ' (' + r.code + ')' : '');
    function filtered() {
        const q = $('ctQ').value.trim().toLowerCase(), agent = $('ctAgent').value, car = $('ctCar').value, kind = $('ctKind').value;
        const debt = $('ctDebt').checked;
        return st.data.rows.filter(r => (!q || (r.name || '').toLowerCase().includes(q) || (r.code || '').toLowerCase().includes(q))
            && (!agent || r.agent === agent) && (!car || r.car_code === car) && (!kind || r.tare_id === kind) && (!debt || r.balance > 0));
    }
    function fillSelect(id, values, label) {
        const el = $(id), cur = el.value;
        el.innerHTML = '<option value="">Բոլորը</option>' + values.map(v => '<option value="' + esc(v) + '">' + esc(label ? label(v) : v) + '</option>').join('');
        el.value = values.includes(cur) ? cur : '';
    }

    // ---------- Загрузка ----------
    async function load() {
        const seq = ++st.seq;
        $('ctRefresh').classList.add('is-busy');
        try {
            const d = await api('/api/courier/admin/tare');
            if (seq !== st.seq) return;
            st.data = d;
            showError('');
            render();
        } catch (e) {
            if (seq !== st.seq) return;
            showError(e.message);
            if (!st.data) $('ctBody').innerHTML = '';
        } finally {
            if (seq === st.seq) $('ctRefresh').classList.remove('is-busy');
        }
    }

    function render() {
        const d = st.data;
        const uniq = (f) => [...new Set(d.rows.map(f).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'hy'));
        fillSelect('ctAgent', uniq(r => r.agent));
        fillSelect('ctCar', uniq(r => r.car_code));
        fillSelect('ctKind', d.kinds.map(k => k.tare_id), kindName);
        $('ctPeriod').textContent = d.first_day ? 'Տերմինալների տվյալներ՝ ' + dateHy(d.first_day) + ' – ' + dateHy(d.last_day) : 'Տերմինալներից դեռ տվյալներ չկան';
        const notes = [];
        if (d.links_failed) notes.push('ERP-ն հասանելի չէ — մասնակի առաքումների տարան հաշվված է մոտավոր (ըստ քաշի, նշված է ≈)։');
        if (d.lost) notes.push(fmt(d.lost) + ' տարայի նշում չի կապվել խանութի հետ (կետը չի գտնվել)։');
        $('ctNotes').innerHTML = notes.map(t => '<p class="ct-note"><i class="fas fa-circle-exclamation" aria-hidden="true"></i>' + esc(t) + '</p>').join('');
        renderKpis();
        renderTable();
    }
    function renderKpis() {
        const rows = st.data.rows;
        const stores = new Set(rows.filter(r => r.balance > 0).map(r => r.customer_id)).size;
        const byKind = {};
        rows.forEach(r => { if (r.balance > 0) byKind[r.tare_id] = (byKind[r.tare_id] || 0) + r.balance; });
        const kinds = Object.keys(byKind).sort((a, b) => byKind[b] - byKind[a]);
        $('ctKpis').innerHTML = '<div class="ct-kpi"><span class="ct-kpi-l">Խանութներ, որոնցում մեր տարան է</span><span class="ct-kpi-v">' + fmt(stores) + '</span></div>'
            + '<div class="ct-kpi ct-kpi-wide"><span class="ct-kpi-l">Ընդամենը խանութներում</span><span class="ct-kpi-kinds">'
            + (kinds.length ? kinds.map(t => '<span class="ct-chip"><b>' + esc(fmt(byKind[t])) + '</b> ' + esc(kindName(t)) + '</span>').join('') : '<span class="ct-mute">—</span>')
            + '</span></div>';
    }
    function balanceCell(r) {
        const cls = r.balance > 0 ? 'is-out' : r.balance < 0 ? 'is-neg' : 'is-zero';
        return '<td class="num ct-bal ' + cls + '" data-label="Մնացորդ">' + (r.approx ? '<span title="Մոտավոր (ըստ քաշի)">≈ </span>' : '') + esc(fmt(r.balance)) + '</td>';
    }
    function openingCell(r, i) {
        return '<td class="num ct-open" data-label="Սկզբնական">' + (r.opening === null ? '<span class="ct-mute">—</span>'
            : esc(fmt(r.opening)) + '<small>' + esc(dateHy(r.as_of)) + '</small>')
            + '<button type="button" class="ct-edit" data-edit="' + i + '" aria-label="Փոխել սկզբնական մնացորդը՝ ' + esc(storeText(r) + ', ' + kindName(r.tare_id))
            + '" title="Փոխել սկզբնական մնացորդը"><i class="fas fa-pen" aria-hidden="true"></i></button></td>';
    }
    function renderTable() {
        const rows = filtered();
        st.view = rows;
        const part = rows.slice(0, st.shown);
        $('ctBody').innerHTML = part.length ? part.map((r, i) => '<tr>'
            + '<td class="rt-cell-name" data-label="Խանութ"><button type="button" class="ct-store" data-cid="' + esc(r.customer_id) + '">' + esc(r.name || '—')
            + '</button><small class="ct-code">' + esc(r.code) + '</small></td>'
            + '<td data-label="Մենեջեր">' + esc(r.agent || '—') + '</td><td data-label="Մեքենա">' + esc(r.car_code || '—') + '</td>'
            + '<td data-label="Տարա">' + esc(kindName(r.tare_id)) + '</td>' + openingCell(r, i)
            + '<td class="num" data-label="Տարվել է">' + esc(fmt(r.went)) + '</td><td class="num" data-label="Հետ է վերցվել">' + esc(fmt(r.returned)) + '</td>'
            + balanceCell(r) + '</tr>').join('')
            : '<tr><td colspan="8" class="ct-empty">' + (st.data.rows.length ? 'Ֆիլտրին համապատասխան խանութներ չկան' : 'Տարայի շարժ դեռ չկա') + '</td></tr>';
        const more = rows.length - part.length;
        $('ctMore').hidden = more <= 0;
        $('ctMore').innerHTML = more > 0 ? 'Ցույց է տրված ' + esc(fmt(part.length)) + '՝ ' + esc(fmt(rows.length)) + '-ից։ <button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" id="ctShowMore">Ցույց տալ ևս</button>' : '';
        announce('Տողեր՝ ' + rows.length);
    }

    // ---------- Начальный остаток: правка в строке ----------
    let formNo = 0;
    function openingForm(r, onDone) {
        const form = document.createElement('form'), n = ++formNo;
        form.className = 'ct-oform';
        form.innerHTML = '<label class="rt-sr-only" for="ctOq' + n + '">Քանակ</label><input id="ctOq' + n + '" class="rt-input" type="number" min="0" step="0.01" required value="' + esc(r.opening ?? '') + '">'
            + '<label class="rt-sr-only" for="ctOd' + n + '">Ամսաթիվ</label><input id="ctOd' + n + '" class="rt-input" type="date" required max="' + today() + '" value="' + esc(r.as_of || today()) + '">'
            + '<button type="submit" class="rt-btn rt-btn-primary rt-btn-sm">Պահել</button>'
            + (r.opening !== null ? '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-act="del">Հեռացնել</button>' : '')
            + '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-act="cancel">Չեղարկել</button><p class="ct-ferr" role="alert"></p>';
        const err = form.querySelector('.ct-ferr');
        async function send(qty) {
            form.querySelectorAll('button,input').forEach(b => { b.disabled = true; });
            try {
                await api('/api/courier/admin/tare/opening', { customer_id: r.customer_id, tare_id: r.tare_id, qty,
                    as_of: form.querySelector('input[type=date]').value });
                announce('Սկզբնական մնացորդը պահված է');
                onDone(true);
            } catch (e) {
                err.textContent = e.message;
                form.querySelectorAll('button,input').forEach(b => { b.disabled = false; });
            }
        }
        form.addEventListener('submit', (e) => { e.preventDefault(); send(form.querySelector('input[type=number]').value); });
        form.addEventListener('click', (e) => {
            const b = e.target.closest('button[data-act]');
            if (!b) return;
            if (b.dataset.act === 'cancel') onDone(false); else send(null);
        });
        form.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); onDone(false); } });
        return form;
    }
    function editRow(i, btn) {
        const r = st.view[i];
        const cell = btn.closest('td');
        const saved = cell.innerHTML;
        cell.innerHTML = '';
        cell.appendChild(openingForm(r, (changed) => {
            if (changed) { load(); return; }
            cell.innerHTML = saved;
            const again = cell.querySelector('.ct-edit');
            if (again) again.focus();
        }));
        cell.querySelector('input').focus();
    }

    // ---------- История магазина ----------
    async function openStore(cid) {
        st.store = cid;
        const row = st.data.rows.find(r => r.customer_id === cid) || {};
        $('ctStoreTitle').textContent = storeText(row);
        $('ctStoreBody').innerHTML = '<div class="rt-loading"><div class="rt-spinner"></div>Բեռնվում է…</div>';
        if (!$('ctStoreDlg').open) $('ctStoreDlg').showModal();
        try {
            const h = await api('/api/courier/admin/tare/history?customer=' + encodeURIComponent(cid));
            if (st.store !== cid) return;
            renderStore(row, h);
        } catch (e) {
            $('ctStoreBody').innerHTML = '<p class="ct-ferr">' + esc(e.message) + '</p>';
        }
    }
    function renderStore(row, h) {
        const meta = [row.agent, row.car_code].filter(Boolean).join(' · ');
        const bal = Object.keys(h.balance);
        let html = (meta ? '<p class="ct-meta">' + esc(meta) + '</p>' : '')
            + '<div class="ct-chips">' + (bal.length ? bal.map(t => '<span class="ct-chip"><b>' + esc(fmt(h.balance[t])) + '</b> ' + esc(kindName(t)) + '</span>').join('')
                : '<span class="ct-mute">Մնացորդ չկա</span>') + '</div>'
            + '<h3>Սկզբնական մնացորդներ</h3><ul class="ct-olist">'
            + (h.openings.length ? h.openings.map(o => '<li><b>' + esc(kindName(o.tare_id)) + '</b>՝ ' + esc(fmt(o.qty)) + ' · ' + esc(dateHy(o.as_of)) + ' դրությամբ'
                + (o.updated_by ? ' <small>(' + esc(o.updated_by) + ')</small>' : '') + '</li>').join('') : '<li class="ct-mute">Չկան — հաշվարկը տերմինալների առաջին օրից է</li>')
            + '</ul><form class="ct-addform" id="ctAddForm"><label><span>Տարա</span><select class="rt-select" id="ctAddKind" required>'
            + st.data.kinds.map(k => '<option value="' + esc(k.tare_id) + '">' + esc(k.name) + '</option>').join('') + '</select></label>'
            + '<label><span>Քանակ</span><input class="rt-input" id="ctAddQty" type="number" min="0" step="0.01" required></label>'
            + '<label><span>Ամսաթիվ (օրվա վերջում)</span><input class="rt-input" id="ctAddDate" type="date" required max="' + today() + '" value="' + today() + '"></label>'
            + '<button type="submit" class="rt-btn rt-btn-primary rt-btn-sm">Պահել</button><p class="ct-ferr" role="alert" id="ctAddErr"></p></form>'
            + '<h3>Ըստ օրերի</h3>';
        const lines = [];
        h.days.forEach(d => Object.keys({ ...d.went, ...d.returned }).sort().forEach(t => lines.push({ d, t })));
        html += lines.length ? '<div class="rt-table-scroll"><table class="rt-table ct-hist"><thead><tr><th scope="col">Օր</th><th scope="col">Մեքենա</th><th scope="col">Փաստաթուղթ</th>'
            + '<th scope="col">Տարա</th><th scope="col" class="num">Տարվել է</th><th scope="col" class="num">Հետ է վերցվել</th><th scope="col" class="num">Մնացորդ</th></tr></thead><tbody>'
            + lines.map(({ d, t }) => '<tr><td>' + esc(dateHy(d.date)) + '</td><td>' + esc(d.cars.join(', ') || '—') + '</td><td>' + esc(d.docs.join(', ') || '—') + '</td>'
                + '<td>' + esc(kindName(t)) + '</td><td class="num">' + (d.approx && d.went[t] ? '≈ ' : '') + esc(fmt(d.went[t] || 0)) + '</td><td class="num">' + esc(fmt(d.returned[t] || 0)) + '</td>'
                + '<td class="num">' + (d.counted[t] ? esc(fmt(d.balance[t])) : '<span class="ct-mute" title="Մտնում է սկզբնական մնացորդի մեջ">մնացորդում</span>') + '</td></tr>').join('')
            + '</tbody></table></div>' : '<p class="ct-mute">Տերմինալներից տարայի շարժ չկա</p>';
        $('ctStoreBody').innerHTML = html;
        $('ctAddForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = e.target.querySelector('button[type=submit]');
            btn.disabled = true;
            try {
                await api('/api/courier/admin/tare/opening', { customer_id: h.customer_id, tare_id: $('ctAddKind').value,
                    qty: $('ctAddQty').value, as_of: $('ctAddDate').value });
                announce('Սկզբնական մնացորդը պահված է');
                await load();
                openStore(h.customer_id);
            } catch (x) {
                $('ctAddErr').textContent = x.message;
                btn.disabled = false;
            }
        });
    }

    // ---------- Импорт CSV / Excel ----------
    // Столбцы A–D: код магазина, вид тары, количество, дата; первая строка — заголовок, если в C не число
    function cellText(v) {
        if (v instanceof Date) return iso(v);
        return v === null || v === undefined ? '' : String(v).trim();
    }
    // CSV — свой разбор (UTF-8; разделитель «;», табуляция или «,» — по первой строке; кавычки и "" внутри них)
    function parseCsv(text) {
        const src = text.replace(/^﻿/, '');
        const first = src.split(/\r?\n/, 1)[0] || '';
        const sep = first.includes(';') ? ';' : first.includes('\t') ? '\t' : ',';
        const out = [];
        let row = [], cell = '', quoted = false;
        for (let i = 0; i < src.length; i++) {
            const c = src[i];
            if (quoted) {
                if (c === '"' && src[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') quoted = false; else cell += c;
            } else if (c === '"' && !cell) quoted = true;
            else if (c === sep) { row.push(cell.trim()); cell = ''; }
            else if (c === '\n' || c === '\r') {
                if (c === '\r' && src[i + 1] === '\n') i++;
                row.push(cell.trim()); out.push(row); row = []; cell = '';
            } else cell += c;
        }
        if (cell || row.length) { row.push(cell.trim()); out.push(row); }
        return out;
    }
    async function readFile(file) {
        if (file.name.toLowerCase().endsWith('.csv')) return parseCsv(await file.text());
        if (typeof window.XLSX === 'undefined') throw new Error('Excel-ի գրադարանը չբեռնվեց — պահեք ֆայլը CSV ձևաչափով');
        const book = XLSX.read(await file.arrayBuffer(), { type: 'array', cellDates: true });
        return XLSX.utils.sheet_to_json(book.Sheets[book.SheetNames[0]], { header: 1, raw: true, defval: '' });
    }
    function toRows(table) {
        const rows = [];
        table.forEach((r, i) => {
            const cells = (r || []).slice(0, 4).map(cellText);
            if (cells.every(c => !c)) return;
            if (i === 0 && num(String(cells[2]).replace(',', '.').replace(/\s/g, '')) === null) return;   // заголовок
            rows.push({ row: i + 1, code: cells[0] || '', tare: cells[1] || '', qty: cells[2] || '', as_of: cells[3] || '' });
        });
        return rows;
    }
    async function importFile(file) {
        showError('');
        let rows;
        try { rows = toRows(await readFile(file)); } catch (e) { showError(e.message || 'Ֆայլը չի կարդացվում'); return; }
        if (!rows.length) { showError('Ֆայլում տողեր չկան'); return; }
        st.preview = rows;
        $('ctImportBody').innerHTML = '<div class="rt-loading"><div class="rt-spinner"></div>Ստուգում եմ…</div>';
        $('ctImportApply').disabled = true;
        $('ctImportDlg').showModal();
        try {
            renderPreview(await api('/api/courier/admin/tare/import', { rows }), file.name);
        } catch (e) {
            $('ctImportBody').innerHTML = '<p class="ct-ferr">' + esc(e.message) + '</p>';
        }
    }
    function renderPreview(p, name) {
        const replaced = p.rows.filter(r => r.old).length;
        let html = '<p class="ct-meta">' + esc(name) + ' · ' + esc(fmt(p.rows.length)) + ' տող կգրանցվի' + (replaced ? ', որից ' + esc(fmt(replaced)) + '-ը կփոխարինի առկա մնացորդը' : '') + '</p>';
        if (p.errors.length) {
            html += '<p class="ct-ferr"><b>' + esc(fmt(p.errors.length)) + ' սխալ</b> — ուղղեք ֆայլը և կրկին ներմուծեք։ Մինչ այդ ոչինչ չի գրանցվի։</p>'
                + '<div class="rt-table-scroll ct-prev"><table class="rt-table"><thead><tr><th scope="col">Տող</th><th scope="col">Սյունակ</th><th scope="col">Սխալ</th></tr></thead><tbody>'
                + p.errors.map(e => '<tr><td>' + esc(e.row) + '</td><td>' + esc(FIELD[e.field] || e.field) + '</td><td>' + esc(e.error) + '</td></tr>').join('') + '</tbody></table></div>';
        }
        if (p.rows.length) {
            html += '<div class="rt-table-scroll ct-prev"><table class="rt-table"><thead><tr><th scope="col">Կոդ</th><th scope="col">Խանութ</th><th scope="col">Տարա</th>'
                + '<th scope="col" class="num">Քանակ</th><th scope="col">Ամսաթիվ</th><th scope="col">Հիմա</th></tr></thead><tbody>'
                + p.rows.map(r => '<tr><td>' + esc(r.code) + '</td><td>' + esc(r.name) + '</td><td>' + esc(r.tare_name) + '</td><td class="num">' + esc(fmt(r.qty)) + '</td>'
                    + '<td>' + esc(dateHy(r.as_of)) + '</td><td>' + (r.old ? esc(fmt(r.old.qty) + ' · ' + dateHy(r.old.as_of)) : '<span class="ct-mute">նոր</span>') + '</td></tr>').join('')
                + '</tbody></table></div>';
        }
        $('ctImportBody').innerHTML = html;
        $('ctImportApply').disabled = !!p.errors.length || !p.rows.length;
    }
    async function applyImport() {
        const btn = $('ctImportApply');
        btn.disabled = true;
        try {
            const r = await api('/api/courier/admin/tare/import', { rows: st.preview, apply: true });
            $('ctImportDlg').close();
            announce('Գրանցված է՝ ' + r.rows.length + ' տող');
            load();
        } catch (e) {
            if (e.body && Array.isArray(e.body.errors)) renderPreview({ rows: [], errors: e.body.errors }, '');
            else $('ctImportBody').insertAdjacentHTML('afterbegin', '<p class="ct-ferr">' + esc(e.message) + '</p>');
        }
    }

    // ---------- Старт ----------
    function init() {
        $('ctRefresh').addEventListener('click', load);
        ['ctQ', 'ctAgent', 'ctCar', 'ctKind', 'ctDebt'].forEach(id => $(id).addEventListener(id === 'ctQ' ? 'input' : 'change', () => {
            st.shown = PAGE;
            if (st.data) renderTable();
        }));
        $('ctBody').addEventListener('click', (e) => {
            const s = e.target.closest('.ct-store');
            if (s) { openStore(Number(s.dataset.cid)); return; }
            const b = e.target.closest('.ct-edit');
            if (b) editRow(Number(b.dataset.edit), b);
        });
        $('ctMore').addEventListener('click', (e) => {
            if (e.target.closest('#ctShowMore')) { st.shown += PAGE; renderTable(); }
        });
        $('ctImport').addEventListener('click', () => { $('ctFile').value = ''; $('ctFile').click(); });
        $('ctFile').addEventListener('change', () => { const f = $('ctFile').files[0]; if (f) importFile(f); });
        $('ctImportCancel').addEventListener('click', () => $('ctImportDlg').close());
        $('ctImportApply').addEventListener('click', applyImport);
        $('ctStoreDlg').addEventListener('close', () => { st.store = null; });
        load();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

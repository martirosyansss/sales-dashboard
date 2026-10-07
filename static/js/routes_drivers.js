/* «Վարորդներ» /routes/drivers — показатели водителей за период (как driver analytics Omnitracs / Routific).
   API: GET /api/routes/drivers/scorecard?from=&to= (расчёт — route_optimizer/scorecard.py; здесь только показ).
   Сортировка по любому столбцу, строка водителя раскрывает разбивку по дням и опоздавшие магазины.
   Всё, что пришло с сервера (имена, магазины, машины), выводится только через textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const num = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v));
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const COLS = 9;
    const ROLE = { helper: 'առաքիչ' };
    const REASON = { eta: 'պլանից ուշ', window: 'ընդունման ժամից ուշ', early: 'ընդունման ժամից շուտ' };

    const state = { data: null, sort: { key: 'stops', dir: -1 }, open: new Set(), seq: 0, today: null };

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c)); });
        return el;
    }
    const icon = (cls) => h('i', { class: 'fas ' + cls, 'aria-hidden': 'true' });

    function showError(text) { $('drAlert').hidden = !text; $('drAlertText').textContent = text || ''; }
    function say(text) { $('drStatus').textContent = text; }

    // Ответы дашборда (вход, доступ) — по-русски: свой армянский текст по коду ответа
    async function api(url) {
        let resp;
        try { resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } }); }
        catch (e) { throw new Error('Սերվերը հասանելի չէ։ Ստուգեք կապը։'); }
        if (resp.status === 401) {
            window.location.assign('/login?next=' + encodeURIComponent('/routes/drivers'));
            throw new Error('Մուտք գործեք նորից։');
        }
        let body = null;
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const text = body && typeof body.error === 'string' && /[Ա-֏]/.test(body.error) ? body.error : null;
            if (resp.status === 403) throw new Error('Մուտքն արգելված է։');
            throw new Error(text || 'Սերվերի սխալ (' + resp.status + ')։ Կրկնեք մի փոքր ուշ։');
        }
        return body;
    }

    // --- даты ---
    const iso = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    const parseIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const dmy = (s) => (typeof s === 'string' && s.length === 10 ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    function spanRange(days) {
        const to = state.today ? parseIso(state.today) : new Date();
        const from = new Date(to.getFullYear(), to.getMonth(), to.getDate() - (days - 1));
        return [iso(from), iso(to)];
    }

    // --- показ значений ---
    function pctClass(p) { return p === null ? 'is-mute' : p >= 90 ? 'is-good' : p >= 75 ? 'is-warn' : 'is-bad'; }
    function money(v) { const n = num(v); return n === null ? '—' : fmt(Math.round(n)) + ' ֏'; }
    function cashCell(c, cls) {
        const td = h('td', { class: 'num' + (cls ? ' ' + cls : '') });
        if (!c) { td.textContent = '—'; td.classList.add('is-mute'); return td; }
        const short = num(c.short) || 0;
        td.append(h('span', { class: short > 0.5 ? 'is-bad' : (short < -0.5 ? 'is-warn' : ''), text: money(short) }));
        td.append(h('small', { text: 'վերցրել է՝ ' + money(c.collected) }));
        if (c.diff !== null && c.diff !== undefined) {
            const d = num(c.diff) || 0;
            td.append(h('small', { class: Math.abs(d) > 0.5 ? 'is-bad' : '', text: 'հանձնել − վերցրել՝ ' + (d > 0 ? '+' : '') + money(d) }));
        }
        td.title = 'Պետք էր վերցնել կանխիկ՝ ' + money(c.expected) + ', չի վերցվել՝ ' + money(short);
        return td;
    }
    function onTimeCell(pct, ok, rated) {
        const p = num(pct);
        return h('td', { class: 'num' }, h('span', { class: pctClass(p), text: p === null ? '—' : fmt(p) + '%' }),
            h('small', { text: rated ? fmt(ok) + '/' + fmt(rated) : 'գնահատված չէ' }));
    }
    function lateCell(mean, n) {
        const m = num(mean);
        return h('td', { class: 'num' }, h('span', { class: m === null ? 'is-mute' : '', text: m === null ? '—' : fmt(m) + ' ր' }),
            n ? h('small', { text: fmt(n) + ' խանութ' }) : null);
    }
    const prCell = (p, r) => h('td', { class: 'num' }, h('span', { class: p + r ? '' : 'is-mute', text: fmt(p) + ' / ' + fmt(r) }));
    const kmCell = (km) => h('td', { class: 'num' + (num(km) === null ? ' is-mute' : ''), text: num(km) === null ? '—' : fmt(km) });

    // --- сортировка ---
    function sortValue(r, key) {
        if (key === 'name') return r.name || '';
        if (key === 'partial') return (r.partial || 0) + (r.refused || 0);
        if (key === 'cash') return r.cash ? num(r.cash.short) : null;
        return num(r[key]);
    }
    function sorted(rows) {
        const { key, dir } = state.sort;
        return rows.slice().sort((a, b) => {
            const x = sortValue(a, key), y = sortValue(b, key);
            if (x === null && y === null) return a.name.localeCompare(b.name, 'hy');
            if (x === null) return 1;            // пусто — всегда внизу
            if (y === null) return -1;
            const c = typeof x === 'string' ? x.localeCompare(y, 'hy') : x - y;
            return c !== 0 ? c * dir : a.name.localeCompare(b.name, 'hy');
        });
    }
    function markSort() {
        document.querySelectorAll('#drTable thead th').forEach(th => {
            if (th.dataset.key === state.sort.key) th.setAttribute('aria-sort', state.sort.dir > 0 ? 'ascending' : 'descending');
            else th.removeAttribute('aria-sort');
        });
    }

    // --- разбивка по дням ---
    function lateList(items) {
        if (!items || !items.length) return h('span', { class: 'is-mute', text: '—' });
        return h('ul', { class: 'dr-late' }, items.map(x => h('li', {},
            h('span', { class: 'n', text: x.name || 'Խանութ' }),
            h('span', { class: 't', text: 'պլան ' + (x.planned || '—') + ' → փաստ ' + (x.arrive || '—') }),
            h('span', { class: 'd' + (x.reason === 'early' ? ' is-early' : ''),
                text: (x.reason === 'early' ? '' : '+' + fmt(x.delay_min) + ' ր, ') + (REASON[x.reason] || '') }))));
    }
    function detailTable(r) {
        const head = ['Ամսաթիվ', 'Մեքենա', 'Խանութներ', 'Ժամանակին', 'Միջին ուշացում', 'Մասնակի / Հրաժարում', 'Կմ', 'Կանխիկ', 'Տարա',
            'Ուշացած խանութներ'];
        const numeric = new Set([2, 3, 4, 5, 6, 7, 8]);
        return h('table', { class: 'dr-days' },
            h('caption', { text: r.name + ' — ըստ օրերի' }),
            h('thead', {}, h('tr', {}, head.map((t, i) => h('th', { scope: 'col', class: numeric.has(i) ? 'num' : null, text: t })))),
            h('tbody', {}, r.detail.map(d => h('tr', {},
                h('td', { text: dmy(d.date) }),
                h('td', { text: (d.cars || []).join(', ') || '—' }),
                h('td', { class: 'num', text: fmt(d.stops) }),
                onTimeCell(d.on_time_pct, d.on_time, d.rated),
                lateCell(d.late_mean_min, 0),
                prCell(d.partial, d.refused),
                kmCell(d.km),
                cashCell(d.cash),
                h('td', { class: 'num', text: fmt(d.tare) }),
                h('td', {}, lateList(d.late))))));
    }

    function toggle(key) {
        if (state.open.has(key)) state.open.delete(key); else state.open.add(key);
        const row = document.querySelector('.dr-row[data-key="' + CSS.escape(key) + '"]');
        if (!row) return;
        const open = state.open.has(key);
        row.classList.toggle('is-open', open);
        row.querySelector('.dr-name').setAttribute('aria-expanded', String(open));
        row.nextElementSibling.hidden = !open;
    }

    // --- таблица ---
    function renderRows() {
        const body = $('drRows');
        const rows = state.data ? state.data.drivers : [];
        markSort();
        if (!rows.length) {
            body.replaceChildren(h('tr', {}, h('td', { colspan: COLS, class: 'rt-empty', text: 'Այս ժամանակահատվածում «Առաքիչ» հավելվածի նշումներ չկան։' })));
            return;
        }
        const out = [];
        sorted(rows).forEach((r, i) => {
            const open = state.open.has(r.key);
            const id = 'drDetail' + i;
            const name = h('button', { type: 'button', class: 'dr-name', 'aria-expanded': String(open), 'aria-controls': id },
                icon('fa-chevron-right'), h('span', { text: r.name }),
                ROLE[r.role] ? h('span', { class: 'rt-badge b-manual', text: ROLE[r.role] }) : null);
            const tr = h('tr', { class: 'dr-row' + (open ? ' is-open' : ''), 'data-key': r.key },
                h('th', { scope: 'row', class: 'dr-cell-name' }, name),
                h('td', { class: 'num', text: fmt(r.days) }),
                h('td', { class: 'num', text: fmt(r.stops) }),
                onTimeCell(r.on_time_pct, r.on_time, r.rated),
                lateCell(r.late_mean_min, r.late),
                prCell(r.partial, r.refused),
                kmCell(r.km),
                cashCell(r.cash),
                h('td', { class: 'num', text: fmt(r.tare) }));
            const detail = h('tr', { class: 'dr-detail', id }, h('td', { colspan: COLS }, detailTable(r)));
            detail.hidden = !open;
            out.push(tr, detail);
        });
        body.replaceChildren(...out);
    }

    function renderSummary(d) {
        const drivers = d.drivers.filter(r => r.role === 'driver');
        const sum = (k) => drivers.reduce((s, r) => s + (num(r[k]) || 0), 0);
        const rated = sum('rated'), ok = sum('on_time');
        const kmKnown = drivers.some(r => num(r.km) !== null);
        const kpi = (label, val, sub) => h('div', { class: 'dr-kpi' }, h('div', { class: 'dr-kpi-label', text: label }),
            h('div', { class: 'dr-kpi-val' }, val, sub ? h('small', { text: sub }) : null));
        const pct = rated ? Math.round(1000 * ok / rated) / 10 : null;
        $('drKpis').replaceChildren(
            kpi('Վարորդներ', fmt(drivers.length)),
            kpi('Փակված խանութներ', fmt(sum('stops'))),
            kpi('Ժամանակին', h('span', { class: pctClass(pct), text: pct === null ? '—' : fmt(pct, 1) + '%' }),
                rated ? fmt(ok) + '/' + fmt(rated) : null),
            kpi('Կմ (GPS)', kmKnown ? fmt(sum('km')) : '—'));
        $('drKpis').hidden = false;
        const c = d.coverage || {};
        const parts = [];
        if (c.closed) {
            parts.push('«Ժամանակին»-ը գնահատված է ' + fmt(c.rated) + ' խանութի համար ' + fmt(c.closed) + ' փակվածից։');
            if (c.no_gps) parts.push('GPS-ով ժամանում չկա՝ ' + fmt(c.no_gps) + '։');
            if (c.no_eta) parts.push('Պլանի ժամ չկա՝ ' + fmt(c.no_eta) + '։');
            if (c.unattributed) parts.push('Վարորդը հայտնի չէ՝ ' + fmt(c.unattributed) + '։');
            parts.push('Մեքենա-օրեր՝ ' + fmt(c.car_days) + ', որից GPS-ով՝ ' + fmt(c.car_days_gps) + '։');
            if (num(c.km_unassigned) > 0.5) parts.push(fmt(c.km_unassigned) + ' կմ՝ առանց փակված խանութի (վարորդին չի գրվել)։');
        }
        if (!d.gps) parts.push('GPS-ի տվյալները միացված չեն․ կմ-ն և «Ժամանակին»-ը չեն հաշվվում։');
        $('drCoverage').textContent = parts.join(' ');
        $('drSlack').textContent = fmt(d.rules && d.rules.late_slack_min);
        $('drTableTitleText').textContent = 'Վարորդներ՝ ' + dmy(d.from) + ' – ' + dmy(d.to);
    }

    async function load(from, to) {
        const seq = ++state.seq;
        showError('');
        say('Բեռնվում է…');
        $('drApply').disabled = true;
        const q = from && to ? '?from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to) : '';
        try {
            const d = await api('/api/routes/drivers/scorecard' + q);
            if (seq !== state.seq) return;
            state.data = d;
            state.today = d.today || state.today;
            $('drFrom').value = d.from;
            $('drTo').value = d.to;
            $('drFrom').max = $('drTo').max = d.today || '';
            renderSummary(d);
            renderRows();
            say('Վարորդներ՝ ' + d.drivers.length);
        } catch (e) {
            if (seq !== state.seq) return;
            showError(e.message);
            say(e.message);
            if (!state.data) $('drRows').replaceChildren(h('tr', {}, h('td', { colspan: COLS, class: 'rt-empty', text: '—' })));
        } finally {
            if (seq === state.seq) $('drApply').disabled = false;
        }
    }

    function checkedSpan(value) {
        document.querySelectorAll('input[name="drSpan"]').forEach(x => { x.checked = x.value === value; });
    }

    function init() {
        document.querySelectorAll('input[name="drSpan"]').forEach(x => x.addEventListener('change', () => {
            if (!x.checked) return;
            const [from, to] = spanRange(Number(x.value));
            load(from, to);
        }));
        $('drPeriod').addEventListener('submit', (ev) => {
            ev.preventDefault();
            const from = $('drFrom').value, to = $('drTo').value;
            if (!from || !to) { showError('Նշեք երկու ամսաթվերը։'); return; }
            if (from > to) { showError('Սկզբի ամսաթիվը չի կարող լինել վերջից ուշ։'); return; }
            const days = Math.round((parseIso(to) - parseIso(from)) / 86400000) + 1;
            const max = state.data && state.data.rules ? state.data.rules.max_days : 92;
            if (days > max) { showError('Ժամանակահատվածը՝ առավելագույնը ' + max + ' օր։'); return; }
            checkedSpan(['7', '30', '90'].find(v => [from, to].join() === spanRange(Number(v)).join()) || '');
            load(from, to);
        });
        document.querySelectorAll('#drTable thead th').forEach(th => th.querySelector('.dr-sort').addEventListener('click', () => {
            const key = th.dataset.key;
            state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === 'name' ? 1 : -1 };
            renderRows();
        }));
        // строка целиком раскрывает разбивку (кнопка имени — для клавиатуры и экранного диктора)
        $('drRows').addEventListener('click', (ev) => {
            const row = ev.target.closest('.dr-row');
            if (row) toggle(row.dataset.key);
        });
        load(null, null);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();

/* «Առաքման արժեք» /routes/cost — стоимость обслуживания магазина против его продаж (№87, п. 6; cost_to_serve.py).
   API: GET /api/routes/cost?days=30|90 или ?from=&to= (магазины, их рейсы, итог, наценка, ставки),
   GET /api/routes/cost.csv (тот же период, Excel), GET/POST /api/routes/cost/margin (средняя наценка, %; пусто — без
   красного). Только администратору. Всё, что пришло с сервера, выводится только через textContent. CSRF-заголовок к fetch
   добавляет base_v2.html. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const fmt = (v, d = 0) => (v === null || v === undefined || !Number.isFinite(Number(v))) ? '—'
        : Number(v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
    const dayHy = (iso) => iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4);
    const HY = /[Ա-֏]/;

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => {
            if (c === null || c === undefined || c === false) return;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        });
        return el;
    }

    // query — строка периода последнего успешного (или запрошенного) расчёта; sort — колонка и направление
    const state = { data: null, query: 'days=30', seq: 0, sort: { key: 'cost', dir: -1 }, open: null, marginLoaded: false };

    function announce(text) { $('ctStatus').textContent = ''; setTimeout(() => { $('ctStatus').textContent = text; }, 30); }
    function showError(text) { $('ctAlert').hidden = !text; $('ctAlertText').textContent = text || ''; }

    class ApiError extends Error {
        constructor(message, errors) { super(message); this.errors = errors || {}; }
    }
    function httpError(status, body) {
        const text = body && typeof body.error === 'string' ? body.error : '';
        if (status === 403) return text === 'csrf' ? 'Էջը հնացել է՝ թարմացրեք այն և կրկնեք։' : 'Այս էջը ձեզ թույլատրված չէ։';
        if (status === 400 && HY.test(text)) return text;
        if (status === 400 || status === 415) return 'Հարցումը չընդունվեց։ Թարմացրեք էջը և կրկնեք։';
        if (status === 503) return 'ERP տվյալների բազան հասանելի չէ, առաքման արժեքը հնարավոր չէ հաշվել։ Կրկնեք մի փոքր ուշ։';
        if (status === 500 && HY.test(text)) return text;
        return 'Սերվերի սխալ (' + status + ')։ Կրկնեք մի փոքր ուշ։';
    }
    function toLogin() {
        window.location.assign('/login?next=' + encodeURIComponent('/routes/cost'));
        return new ApiError('Մուտք գործեք նորից։');
    }
    async function api(url, json) {
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (json !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new ApiError('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
        if (resp.status === 401) throw toLogin();
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const errors = Object.fromEntries(Object.entries((body && body.errors) || {})
                .filter(([, v]) => typeof v === 'string' && HY.test(v)));
            throw new ApiError(httpError(resp.status, body), errors);
        }
        return body;
    }

    // ---------- период ----------
    function setPressed(which) {
        document.querySelectorAll('.ct-period').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.days === which)));
    }
    document.querySelectorAll('.ct-period').forEach(b => b.addEventListener('click', () => {
        if (b.dataset.days === 'custom') {
            setPressed('custom');
            $('ctRange').hidden = false;
            if (state.data) { $('ctFrom').value = state.data.from; $('ctTo').value = state.data.to; }
            $('ctFrom').focus();
            return;
        }
        setPressed(b.dataset.days);
        $('ctRange').hidden = true;
        load('days=' + b.dataset.days);
    }));
    $('ctRange').addEventListener('submit', (ev) => {
        ev.preventDefault();
        const from = $('ctFrom').value, to = $('ctTo').value;
        if (!from || !to) { showError('Նշեք երկու ամսաթվերը։'); return; }
        load('from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to));
    });

    // ---------- загрузка ----------
    async function load(query) {
        const seq = ++state.seq;          // ответ на прежний период не перетирает новый
        state.query = query;
        showError('');
        $('ctLoading').hidden = false;
        $('ctTableWrap').hidden = true;
        $('ctEmpty').hidden = true;
        $('ctNoMatch').hidden = true;
        closeTrips();
        try {
            const data = await api('/api/routes/cost?' + query);
            if (seq !== state.seq) return;
            state.data = data;
            render();
            announce('Առաքման արժեքը հաշվված է՝ ' + dayHy(data.from) + ' – ' + dayHy(data.to));
        } catch (e) {
            if (seq !== state.seq) return;
            state.data = null;                // ERP недоступна — ошибка, а не нули
            clearTiles();
            showError(e.message);
        } finally {
            if (seq === state.seq) $('ctLoading').hidden = true;
        }
    }

    function clearTiles() {
        ['ctTotal', 'ctPct', 'ctRed'].forEach(id => { $(id).textContent = '—'; });
        ['ctTotalSub', 'ctPctSub', 'ctRedSub', 'ctSub'].forEach(id => { $(id).textContent = ''; });
        $('ctPctTile').classList.remove('is-bad');
        $('ctRedTile').classList.remove('is-bad');
        $('ctWarn').hidden = true;
    }

    function render() {
        const d = state.data, t = d.totals;
        const planned = d.sources.sent + d.sources.draft;
        $('ctSub').textContent = dayHy(d.from) + ' – ' + dayHy(d.to) + ' · ' + d.days + ' օր, որից պլանով՝ ' + planned
            + ' · ' + fmt(t.stores) + ' խանութ · ' + fmt(t.trips) + ' երթ';
        $('ctTotal').textContent = fmt(t.cost);
        $('ctTotalSub').replaceChildren('դիզել և մաշվածք ', h('b', { text: fmt(t.fuel) }), ' ֏ + առաքիչ ',
            h('b', { text: fmt(t.crew) }), ' ֏ · ', fmt(t.visits), ' այց');
        $('ctPct').textContent = fmt(t.pct, 2);
        $('ctPctSub').replaceChildren('վաճառք ', h('b', { text: fmt(t.sales) }), ' ֏',
            ...(d.margin !== null ? [' · հավելագին ', h('b', { text: fmt(d.margin, d.margin % 1 ? 1 : 0) + '%' })] : []));
        $('ctPctTile').classList.toggle('is-bad', d.margin !== null && t.pct !== null && t.pct > d.margin);
        $('ctRed').textContent = d.margin === null ? '—' : fmt(t.red);
        $('ctRedTile').classList.toggle('is-bad', d.margin !== null && t.red > 0);
        $('ctRedSub').textContent = d.margin === null ? 'Նշեք միջին հավելագինը՝ կարմիրները տեսնելու համար։'
            : 'որտեղ առաքումը ավելին է, քան ' + fmt(d.margin, d.margin % 1 ? 1 : 0) + '% վաճառքից';
        $('ctRatePoint').textContent = fmt(d.rates.rate_point);
        $('ctRateTonne').textContent = fmt(d.rates.rate_tonne);
        $('ctFuel').textContent = fmt(d.fuel_price) + (d.fuel_price_estimated ? ' (գինը նշված չէ, պայմանական)' : '');
        const s = d.sources;
        const warn = [
            s.draft ? s.draft + ' օր հաշվված է պահպանված պլանով (վարորդներին չի ուղարկվել)։' : '',
            s.broken ? s.broken + ' օրվա պլանը վնասված է և հաշվի չի առնված։' : '',
            t.unpriced_trips ? t.unpriced_trips + ' երթի մեքենան առանց տոննաժի կամ ծախսի է՝ դիզել և մաշվածք չեն հաշվված։' : '',
            d.margin_store_error ? 'Պահպանված հավելագինը վնասված է՝ նշեք այն նորից։' : '',
            d.fuel_price_estimated ? 'Դիզելի գինը նշված չէ կարգավորումներում՝ հաշվված է պայմանական ' + fmt(d.fuel_price) + ' ֏/լ։' : '',
        ].filter(Boolean);
        $('ctWarn').hidden = !warn.length;
        $('ctWarnText').textContent = warn.join(' ');
        $('ctEmpty').hidden = d.rows.length > 0;
        $('ctTableWrap').hidden = d.rows.length === 0;
        renderRows();
    }

    // ---------- таблица: сортировка, поиск ----------
    const TEXT_KEYS = new Set(['code', 'name']);
    function sortedRows() {
        const { key, dir } = state.sort;
        const q = $('ctSearch').value.trim().toLocaleLowerCase();
        const red = $('ctOnlyRed').checked;
        const rows = state.data.rows.map((r, i) => [r, i]).filter(([r]) => (!red || r.red)
            && (!q || (r.name || '').toLocaleLowerCase().includes(q) || (r.code || '').toLocaleLowerCase().includes(q)));
        const val = (r) => {
            if (TEXT_KEYS.has(key)) return r[key] || '';
            if (key === 'pct') return r.pct === null ? (r.cost > 0 ? Infinity : -Infinity) : r.pct;   // без продаж — «бесконечно»
            return r[key];
        };
        rows.sort(([a, ia], [b, ib]) => {
            const x = val(a), y = val(b);
            const c = TEXT_KEYS.has(key) ? String(x).localeCompare(String(y), 'hy') : (x < y ? -1 : x > y ? 1 : 0);
            return c * dir || ia - ib;
        });
        return rows;
    }

    function renderRows() {
        const d = state.data;
        const rows = sortedRows();
        const num = (text, cls) => h('td', { class: 'num' + (cls ? ' ' + cls : ''), text });
        $('ctNoMatch').hidden = rows.length > 0 || d.rows.length === 0;
        $('ctRows').replaceChildren(...rows.map(([r, i]) => {
            const label = r.name || r.code;
            const tr = h('tr', { 'data-i': i, class: (r.red ? 'is-red' : '') + (state.open === i ? ' is-open' : '') },
                h('td', { class: 'txt', text: r.code }),
                h('td', { class: 'txt ct-name' }, h('button', { type: 'button', class: 'rt-linkbtn', text: label,
                    'aria-label': label + '՝ երթերը' }),
                    r.unrouted ? h('span', { class: 'rt-badge b-none', text: 'առանց կոորդինատի' }) : null),
                num(fmt(r.visits)),
                num(fmt(r.fuel)),
                num(fmt(r.crew)),
                num(fmt(r.cost), 'ct-cost'),
                num(fmt(r.per_visit)),
                num(fmt(r.sales)),
                h('td', { class: 'num' + (r.red ? ' is-bad' : '') }, r.pct === null ? (r.cost > 0 ? 'վաճառք չկա' : '—') : fmt(r.pct, 2),
                    r.red ? h('span', { class: 'rt-badge b-danger', text: 'կարմիր' }) : null),
            );
            tr.addEventListener('click', () => openTrips(i));
            return tr;
        }));
        const t = d.totals;
        $('ctTotals').replaceChildren(d.rows.length ? h('tr', null,
            h('td', { colspan: 2, text: 'Ընդամենը' }), num(fmt(t.visits)), num(fmt(t.fuel)), num(fmt(t.crew)), num(fmt(t.cost)),
            num(''), num(fmt(t.sales)), num(fmt(t.pct, 2))) : '');
        document.querySelectorAll('.ct-table thead th').forEach(th => {
            if (th.dataset.key === state.sort.key) th.setAttribute('aria-sort', state.sort.dir > 0 ? 'ascending' : 'descending');
            else th.removeAttribute('aria-sort');
        });
    }

    document.querySelectorAll('.ct-table thead th').forEach(th => th.querySelector('button').addEventListener('click', () => {
        const key = th.dataset.key;
        // текст — сначала по алфавиту, числа — сначала большие
        state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: TEXT_KEYS.has(key) ? 1 : -1 };
        if (state.data) renderRows();
    }));
    $('ctSearch').addEventListener('input', () => { if (state.data) renderRows(); });
    $('ctOnlyRed').addEventListener('change', () => { if (state.data) renderRows(); });

    // ---------- рейсы магазина ----------
    function openTrips(i) {
        const r = state.data.rows[i];
        state.open = i;
        document.querySelectorAll('#ctRows tr').forEach(tr => tr.classList.toggle('is-open', +tr.dataset.i === i));
        $('ctTripsTitle').replaceChildren(h('i', { class: 'fas fa-route', 'aria-hidden': 'true' }),
            (r.name || r.code) + ' · ' + r.code + ' · ' + fmt(r.cost) + ' ֏');
        const num = (text) => h('td', { class: 'num', text });
        $('ctTripRows').replaceChildren(...r.trips.map(v => h('tr', null,
            h('td', { class: 'txt', text: dayHy(v.date) }), h('td', { class: 'txt', text: v.truck }), num('#' + v.trip),
            num(fmt(v.fuel)), num(fmt(v.crew)), num(fmt(v.fuel + v.crew)), num(fmt(v.detour_km, 1)), num(fmt(v.trip_fuel)),
            num(fmt(v.trip_stops)))));
        $('ctTrips').hidden = false;
        $('ctTrips').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
        $('ctTrips').focus({ preventScroll: true });
    }
    function closeTrips() {
        const back = state.open;
        state.open = null;
        $('ctTrips').hidden = true;
        document.querySelectorAll('#ctRows tr.is-open').forEach(tr => tr.classList.remove('is-open'));
        return back;
    }
    $('ctTripsClose').addEventListener('click', () => {
        const i = closeTrips();
        const btn = i === null ? null : document.querySelector('#ctRows tr[data-i="' + i + '"] button');
        if (btn) btn.focus();
    });

    // ---------- наценка ----------
    function fillMargin(body) {
        $('ctMargin').value = body.value === null ? '' : String(body.value);
        state.marginLoaded = true;
        $('ctMarginSave').disabled = false;
        $('ctMarginChanged').textContent = body.store_error
            ? 'Պահպանված հավելագինը վնասված է՝ նշեք և պահպանեք նորից։'
            : body.updated_at ? 'Փոխվել է՝ ' + dayHy(body.updated_at) + ' ' + body.updated_at.slice(11, 16)
                + (body.updated_by ? ', ' + body.updated_by : '') + '։' : '';
    }
    function marginError(text) {
        $('ctMarginErr').textContent = text || '';
        $('ctMargin').classList.toggle('is-invalid', !!text);
        if (text) $('ctMargin').setAttribute('aria-invalid', 'true'); else $('ctMargin').removeAttribute('aria-invalid');
    }
    async function loadMargin() {
        try { fillMargin(await api('/api/routes/cost/margin')); } catch (e) {
            $('ctMarginChanged').textContent = 'Հավելագինը չբեռնվեց՝ ' + e.message;
        }
    }
    $('ctMarginForm').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const raw = $('ctMargin').value.trim();
        // та же проверка, что на сервере (cost_to_serve.check_margin): запрос с заведомо неверным числом не отправляем
        const bad = raw === '' ? '' : !Number.isFinite(Number(raw)) ? 'Լրացրեք թիվը'
            : Number(raw) < 0 || Number(raw) > 100 ? 'Թույլատրելի է 0-ից 100' : '';
        if (bad) { marginError(bad); $('ctMargin').focus(); return; }
        $('ctMarginSave').disabled = true;
        $('ctMarginSaved').textContent = '';
        marginError('');
        try {
            fillMargin(await api('/api/routes/cost/margin', { value: raw === '' ? null : Number(raw) }));
            $('ctMarginSaved').textContent = 'Պահպանվեց';
            await load(state.query);
        } catch (e) {
            if (e.errors.value) { marginError(e.errors.value); $('ctMargin').focus(); } else showError(e.message);
        } finally {
            $('ctMarginSave').disabled = !state.marginLoaded;
        }
    });

    // ---------- CSV: сначала запрос, потом файл — при недоступной ERP ошибка, а не JSON в файле ----------
    $('ctCsv').addEventListener('click', async () => {
        $('ctCsv').disabled = true;
        showError('');
        try {
            let resp;
            try {
                resp = await fetch('/api/routes/cost.csv?' + state.query, { credentials: 'same-origin', cache: 'no-store' });
            } catch (e) { throw new ApiError('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
            if (resp.status === 401) throw toLogin();
            if (!resp.ok) {
                let body = null;
                try { body = await resp.json(); } catch (e) { /* не JSON */ }
                throw new ApiError(httpError(resp.status, body));
            }
            const name = (/filename="([^"]+)"/.exec(resp.headers.get('Content-Disposition') || '') || [])[1] || 'cost-to-serve.csv';
            const url = URL.createObjectURL(await resp.blob());
            const a = h('a', { href: url, download: name });
            document.body.append(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            showError(e.message);
        } finally {
            $('ctCsv').disabled = false;
        }
    });

    loadMargin();
    load(state.query);
})();

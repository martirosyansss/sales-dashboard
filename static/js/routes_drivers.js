/* «Վարորդներ» /routes/drivers — показатели водителей за период (как driver analytics Omnitracs / Routific; №83, №87).
   API: GET /api/routes/drivers/scorecard?from=&to= (расчёт — route_optimizer/scorecard.py; здесь только показ).
   Сортировка по любому столбцу, строка водителя раскрывает разбивку балла и по дням (опоздавшие магазины).
   «Гараж» видит страницу без столбца «Կանխիկ»: сервер не присылает денег (d.cash = false), столбца нет и в разметке.
   Всё, что пришло с сервера (имена, магазины, машины), выводится только через textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const num = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v));
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const signed = (v, d = 0) => { const n = num(v); return n === null ? '—' : (n > 0 ? '+' : '') + fmt(n, d); };
    const ROLE = { helper: 'առաքիչ' };
    const REASON = { eta: 'պլանից ուշ', window: 'ընդունման ժամից ուշ', early: 'ընդունման ժամից շուտ' };
    // составляющие балла (scorecard.WEIGHTS водителя, HELPER_WEIGHTS առաքիչ — №88): подпись и единица значения
    const PART = {
        on_time: ['Ժամանակին', '%'], order: ['Հերթականություն', '%'], speed: ['Արագություն', ' / 100 կմ'],
        stops: ['Կանգառներ խանութից դուրս', ' ր/օր'], liters: ['Վառելիք՝ նորմից', '%'],
        clean: ['Առանց խնդրի (առանց մերժման և պակասի)', '%'], unload: ['Բեռնաթափում՝ նորմի նկատմամբ', '%'],
        day: ['Օրվա տևողություն՝ պլանի նկատմամբ', '%'],
        route: ['Երթուղուն հետևում (պլանային երթուղու կմ-ի բաժինը)', '%'],   // 08.10: live.adherence, բացատրվածները՝ ոչ
    };

    const state = { data: null, sort: { key: 'score', dir: -1 }, open: new Set(), seq: 0, today: null,
        cash: $('drPage').dataset.cash === '1' };
    const cols = () => (state.cash ? 14 : 13);

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
    function scoreClass(s) { return s === null ? 'is-mute' : s >= 80 ? 'is-good' : s >= 60 ? 'is-warn' : 'is-bad'; }
    function money(v) { const n = num(v); return n === null ? '—' : fmt(Math.round(n)) + ' ֏'; }
    const mute = (td) => { td.classList.add('is-mute'); return td; };
    function cashCell(c) {
        const td = h('td', { class: 'num' });
        if (!c) { td.textContent = '—'; return mute(td); }
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
    function orderCell(pct, ordered) {
        const p = num(pct);
        return h('td', { class: 'num' }, h('span', { class: pctClass(p), text: p === null ? '—' : fmt(p) + '%' }),
            ordered ? h('small', { text: fmt(ordered) + ' խանութից' }) : null);
    }
    function speedCell(events, per100) {
        const n = num(events);
        if (n === null) return mute(h('td', { class: 'num', text: '—' }));
        return h('td', { class: 'num' }, h('span', { class: n > 0 ? 'is-bad' : 'is-good', text: fmt(n) + ' անգամ' }),
            num(per100) !== null ? h('small', { text: fmt(per100, 2) + ' / 100 կմ' }) : null);
    }
    function stopCell(min, perDay) {
        const m = num(min);
        if (m === null) return mute(h('td', { class: 'num', text: '—' }));
        return h('td', { class: 'num' }, h('span', { class: m > 0 ? 'is-warn' : 'is-good', text: fmt(m) + ' ր' }),
            num(perDay) !== null ? h('small', { text: 'միջինում ' + fmt(perDay) + ' ր/օր' }) : null);
    }
    function fuelCell(pct, fact, norm) {
        const p = num(pct);
        if (p === null) return mute(h('td', { class: 'num', text: '—' }));
        return h('td', { class: 'num' }, h('span', { class: p > 10 ? 'is-bad' : p > 5 ? 'is-warn' : 'is-good', text: signed(p, 1) + '%' }),
            num(norm) !== null ? h('small', { text: fmt(fact) + ' լ / նորմ ' + fmt(norm) + ' լ' }) : null);
    }
    const prCell = (p, r) => h('td', { class: 'num' }, h('span', { class: p + r ? '' : 'is-mute', text: fmt(p) + ' / ' + fmt(r) }));
    // место по-армянски: 1-ին, 2-րդ, 3-րդ … — среди своей роли (водители и առաքիչ — раздельно)
    const place = (n, role) => fmt(n) + (num(n) === 1 ? '-ին' : '-րդ') + ' '
        + fmt(role === 'helper' ? state.data.ranked_helpers : state.data.ranked) + '-ից';
    const kmCell = (km) => h('td', { class: 'num' + (num(km) === null ? ' is-mute' : ''), text: num(km) === null ? '—' : fmt(km) });

    // разбивка балла: «Ժամանակին 82% → 71 × 41%» для подсказки и раскрытой строки
    function partLine(key, p) {
        const [label, unit] = PART[key] || [key, ''];
        return label + '՝ ' + fmt(p.value, key === 'speed' ? 2 : 1) + unit + ' → ' + fmt(p.score) + ' միավոր × ' + fmt(p.share, 1) + '%';
    }
    function scoreTitle(r) {
        if (!r.enough_data) {
            return num(r.days) < num(state.data.rules.min_days)
                ? 'Քիչ տվյալ՝ ' + fmt(r.days) + ' օր (պետք է առնվազն ' + fmt(state.data.rules.min_days) + ')'
                : 'Քիչ տվյալ՝ ցուցանիշներից ոչ մեկի համար տվյալ չկա (GPS, պլան, լիցքավորումներ)';
        }
        const lines = Object.entries(r.parts || {}).map(([k, p]) => partLine(k, p));
        return lines.length ? lines.join('\n') : 'Ցուցանիշների տվյալ չկա';
    }
    function scoreCell(r) {
        const s = num(r.score);
        const td = h('td', { class: 'num dr-score', title: scoreTitle(r) });
        if (!r.enough_data) { td.append(h('span', { class: 'is-mute dr-few', text: 'քիչ տվյալ' })); return td; }
        td.append(h('span', { class: 'dr-score-val ' + scoreClass(s), text: s === null ? '—' : fmt(s) }));
        if (num(r.rank) !== null) td.append(h('small', { text: place(r.rank, r.role) }));
        return td;
    }

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
            // водители — сверху, առաքիչ — под ними: их баллы считаются по разным показателям и не сравниваются
            if (a.role !== b.role) return a.role === 'driver' ? -1 : 1;
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

    // --- раскрытая строка: балл и дни ---
    function lateList(items) {
        if (!items || !items.length) return h('span', { class: 'is-mute', text: '—' });
        return h('ul', { class: 'dr-late' }, items.map(x => h('li', {},
            h('span', { class: 'n', text: x.name || 'Խանութ' }),
            h('span', { class: 't', text: 'պլան ' + (x.planned || '—') + ' → փաստ ' + (x.arrive || '—') }),
            h('span', { class: 'd' + (x.reason === 'early' ? ' is-early' : ''),
                text: (x.reason === 'early' ? '' : '+' + fmt(x.delay_min) + ' ր, ') + (REASON[x.reason] || '') }))));
    }
    function bar(score) {
        const i = h('i', { class: scoreClass(score) });
        i.style.width = Math.max(0, Math.min(100, score || 0)) + '%';
        return i;
    }
    function partsBlock(r) {
        const items = Object.entries(r.parts || {});
        const head = !r.enough_data ? scoreTitle(r) + '։ Ցուցանիշները՝ տեղեկության համար։'
            : 'Միավոր՝ ' + fmt(r.score) + (num(r.rank) !== null ? ' (' + place(r.rank, r.role) + ')' : '');
        return h('div', { class: 'dr-parts' }, h('p', { class: 'dr-parts-head', text: head }),
            items.length ? h('ul', {}, items.map(([k, p]) => h('li', {},
                h('span', { class: 'l', text: (PART[k] || [k])[0] }),
                h('span', { class: 'v', text: fmt(p.value, k === 'speed' ? 2 : 1) + (PART[k] || ['', ''])[1] }),
                h('span', { class: 'bar', 'aria-hidden': 'true' }, bar(num(p.score))),
                h('span', { class: 's', text: fmt(p.score) + ' × ' + fmt(p.share, 1) + '%' }))))
                : h('p', { class: 'is-mute', text: 'Ցուցանիշների տվյալ չկա (GPS և պլան չկան)։' }));
    }
    function detailTable(r) {
        const head = ['Ամսաթիվ', 'Մեքենա', 'Խանութներ', 'Ժամանակին', 'Միջին ուշացում', 'Հերթականություն', 'Արագություն',
            'Կանգառ խանութից դուրս', 'Վառելիք՝ նորմից', 'Երթուղուն հետևում', 'Մասնակի / Հրաժարում', 'Կմ'].concat(state.cash ? ['Կանխիկ'] : [], ['Տարա', 'Ուշացած խանութներ']);
        return h('table', { class: 'dr-days' },
            h('caption', { text: r.name + ' — ըստ օրերի' }),
            h('thead', {}, h('tr', {}, head.map((t, i) => h('th', { scope: 'col', class: i >= 2 && i < head.length - 1 ? 'num' : null, text: t })))),
            h('tbody', {}, r.detail.map(d => h('tr', {},
                h('td', { text: dmy(d.date) }),
                h('td', { text: (d.cars || []).join(', ') || '—' }),
                h('td', { class: 'num', text: fmt(d.stops) }),
                onTimeCell(d.on_time_pct, d.on_time, d.rated),
                lateCell(d.late_mean_min, 0),
                orderCell(d.order_pct, d.ordered),
                speedCell(d.speed_events, null),
                stopCell(d.offroute_min, null),
                fuelCell(d.liters_vs_norm_pct, d.fuel_fact_l, d.fuel_norm_l),
                h('td', { class: 'num' + (num(d.route_pct) === null ? ' is-mute' : ''), text: num(d.route_pct) === null ? '—' : fmt(d.route_pct, 1) + '%' }),
                prCell(d.partial, d.refused),
                kmCell(d.km),
                state.cash ? cashCell(d.cash) : null,
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
            body.replaceChildren(h('tr', {}, h('td', { colspan: cols(), class: 'rt-empty', text: 'Այս ժամանակահատվածում «Առաքիչ» հավելվածի նշումներ չկան։' })));
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
                scoreCell(r),
                h('td', { class: 'num', text: fmt(r.days) }),
                h('td', { class: 'num', text: fmt(r.stops) }),
                onTimeCell(r.on_time_pct, r.on_time, r.rated),
                lateCell(r.late_mean_min, r.late),
                orderCell(r.order_pct, r.ordered),
                speedCell(r.speed_events, r.speed_per_100km),
                stopCell(r.offroute_min, r.offroute_min_per_day),
                fuelCell(r.liters_vs_norm_pct, r.fuel_fact_l, r.fuel_norm_l),
                prCell(r.partial, r.refused),
                kmCell(r.km),
                state.cash ? cashCell(r.cash) : null,
                h('td', { class: 'num', text: fmt(r.tare) }));
            const detail = h('tr', { class: 'dr-detail', id }, h('td', { colspan: cols() }, partsBlock(r), detailTable(r)));
            detail.hidden = !open;
            out.push(tr, detail);
        });
        body.replaceChildren(...out);
    }

    const kpi = (label, val, sub) => h('div', { class: 'dr-kpi' }, h('div', { class: 'dr-kpi-label', text: label }),
        h('div', { class: 'dr-kpi-val' }, val, sub ? h('small', { text: sub }) : null));

    function renderEta(e) {
        const ok = num(e && e.ok_min);
        $('drEtaBox').hidden = false;
        if (!e || !e.n) {
            $('drEta').replaceChildren(kpi('Գնահատված խանութներ', '—', 'պլանի ժամ և GPS-ով ժամանում չկա'));
            return;
        }
        const within = num(e.within_pct);
        $('drEta').replaceChildren(
            kpi('±' + fmt(ok) + ' րոպեի ընթացքում', h('span', { class: pctClass(within), text: fmt(within, 1) + '%' }),
                fmt(e.within_n) + '/' + fmt(e.n) + ' խանութ'),
            kpi('Շեղման մեդիան', fmt(e.median_abs_min, 1) + ' ր', 'կեսը՝ ավելի քիչ'),
            kpi('Շեղում P80', fmt(e.p80_abs_min, 1) + ' ր', '10-ից 8-ը՝ ավելի քիչ'),
            kpi('Շուտ / ուշ', fmt(e.early_pct, 1) + '% / ' + fmt(e.late_pct, 1) + '%',
                fmt(e.early_n) + ' շուտ · ' + fmt(e.late_n) + ' ուշ'));
    }

    function renderSummary(d) {
        const drivers = d.drivers.filter(r => r.role === 'driver');
        const sum = (k) => drivers.reduce((s, r) => s + (num(r[k]) || 0), 0);
        const rated = sum('rated'), ok = sum('on_time');
        const kmKnown = drivers.some(r => num(r.km) !== null);
        const pct = rated ? Math.round(1000 * ok / rated) / 10 : null;
        $('drKpis').replaceChildren(
            kpi('Վարորդներ', fmt(drivers.length), d.ranked ? fmt(d.ranked) + '-ը՝ միավորով' : null),
            kpi('Փակված խանութներ', fmt(sum('stops'))),
            kpi('Ժամանակին', h('span', { class: pctClass(pct), text: pct === null ? '—' : fmt(pct, 1) + '%' }),
                rated ? fmt(ok) + '/' + fmt(rated) : null),
            kpi('Կմ (GPS)', kmKnown ? fmt(sum('km')) : '—'));
        $('drKpis').hidden = false;
        renderEta(d.eta);
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
        const f = c.fuel || {};
        const fuelDays = (f.terrain || 0) + (f.flat || 0);
        if (fuelDays || f.uncovered || f.no_norm) {
            parts.push('Վառելիքը նորմի հետ համեմատված է ' + fmt(fuelDays) + ' մեքենա-օրում'
                + (f.flat ? ' (' + fmt(f.flat) + '-ում՝ առանց վերելքների)' : '') + '։');
            if (f.uncovered) parts.push('GPS-ը քիչ է ծածկում լիցքավորումների միջև կմ-ը՝ ' + fmt(f.uncovered) + ' մեքենա-օր։');
            if (f.no_norm) parts.push('Մեքենայի նորմը նշված չէ՝ ' + fmt(f.no_norm) + ' մեքենա-օր։');
        }
        const rc = c.route || {};
        if (num(rc.pending) > 0) parts.push('Երթուղուն հետևումը դեռ հաշվվում է՝ ' + fmt(rc.pending) + ' օր (թարմացրեք մի փոքր ուշ)։');
        if (num(rc.no_roads) > 0) parts.push('Երթուղուն հետևումը չի հաշվվում՝ ճանապարհների քարտեզը պատրաստ չէ (' + fmt(rc.no_roads) + ' օր)։');
        if (!d.gps) parts.push('GPS-ի տվյալները միացված չեն․ կմ-ն, «Ժամանակին»-ը, արագությունն ու կանգառները չեն հաշվվում։');
        $('drCoverage').textContent = parts.join(' ');
        const r = d.rules || {};
        $('drSlack').textContent = fmt(r.late_slack_min);
        $('drEtaOk').textContent = fmt(r.eta_ok_min);
        $('drMinDays').textContent = fmt(r.min_days);
        if (r.weights) {
            $('drWeights').textContent = Object.entries(r.weights).map(([k, w]) => (PART[k] || [k])[0].toLowerCase() + ' ' + fmt(w)).join(', ');
        }
        if (r.scale) {
            $('drScale').textContent = Object.entries(r.scale).map(([k, [full, zero]]) => (PART[k] || [k])[0].toLowerCase() + '՝ '
                + fmt(full) + (PART[k] || ['', ''])[1] + ' → 100, ' + fmt(zero) + (PART[k] || ['', ''])[1] + ' → 0').join('; ');
        }
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
            state.cash = state.cash && d.cash === true;   // столбец есть в разметке только у администратора
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
            if (!state.data) $('drRows').replaceChildren(h('tr', {}, h('td', { colspan: cols(), class: 'rt-empty', text: '—' })));
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
            // по умолчанию — «лучше сверху»: меньше опозданий, превышений, стоянок и перерасхода
            const asc = ['name', 'late_mean_min', 'speed_per_100km', 'offroute_min', 'liters_vs_norm_pct'].includes(key);
            state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: asc ? 1 : -1 };
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

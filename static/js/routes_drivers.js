/* «Վարորդներ» /routes/drivers — показатели водителей за период (как driver analytics Omnitracs / Routific; №83, №87).
   API: GET /api/routes/drivers/scorecard?from=&to= (расчёт — route_optimizer/scorecard.py; здесь только показ).
   Сортировка по любому столбцу; строка водителя раскрывает разбивку балла, строки по дням (в тех же столбцах таблицы)
   и опоздавшие магазины. Водители и առաքիչ — двумя группами (их баллы считаются по разным показателям).
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
        day: ['Օրվա տևողություն՝ պլանի նկատմամբ', '%'], route: ['Երթուղուն հետևում', '%'],
    };

    const state = { data: null, sort: { key: 'score', dir: -1 }, open: new Set(), seq: 0, today: null,
        cash: $('drPage').dataset.cash === '1' };
    const cols = () => (state.cash ? 13 : 12);
    const WEEKDAY = ['Կիր', 'Երկ', 'Երք', 'Չրք', 'Հնգ', 'Ուրբ', 'Շբթ'];

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

    // --- показ значений: значение крупно, под ним — пояснение мелко ---
    function pctClass(p) { return p === null ? 'is-mute' : p >= 90 ? 'is-good' : p >= 75 ? 'is-warn' : 'is-bad'; }
    function scoreClass(s) { return s === null ? 'is-mute' : s >= 80 ? 'is-good' : s >= 60 ? 'is-warn' : 'is-bad'; }
    function money(v) { const n = num(v); return n === null ? '—' : fmt(Math.round(n)) + ' ֏'; }
    const dash = () => h('td', { class: 'num is-mute', text: '—' });
    const val = (cls, text) => h('span', { class: 'v' + (cls ? ' ' + cls : ''), text });
    const sub = (text) => (text ? h('small', { text }) : null);
    function cashCell(c) {
        if (!c) return dash();
        const short = num(c.short) || 0;
        const td = h('td', { class: 'num' }, val(short > 0.5 ? 'is-bad' : (short < -0.5 ? 'is-warn' : ''), money(short)),
            sub('վերցրել է՝ ' + money(c.collected)));
        if (c.diff !== null && c.diff !== undefined) {
            const d = num(c.diff) || 0;
            td.append(h('small', { class: Math.abs(d) > 0.5 ? 'is-bad' : '', text: 'հանձնել − վերցրել՝ ' + (d > 0 ? '+' : '') + money(d) }));
        }
        td.title = 'Պետք էր վերցնել կանխիկ՝ ' + money(c.expected) + ', չի վերցվել՝ ' + money(short);
        return td;
    }
    // «Ժամանակին» и под ним — сколько оценено и средняя задержка опоздавших
    function onTimeCell(pct, ok, rated, lateMean) {
        const p = num(pct), m = num(lateMean);
        if (!rated) return h('td', { class: 'num is-mute' }, val('', '—'), sub('գնահատված չէ'));
        return h('td', { class: 'num' }, val(pctClass(p), p === null ? '—' : fmt(p) + '%'),
            sub(fmt(ok) + '/' + fmt(rated) + (m !== null && m > 0 ? ' · ուշ +' + fmt(m) + ' ր' : '')));
    }
    function pctCell(pct, note) {
        const p = num(pct);
        if (p === null) return dash();
        return h('td', { class: 'num' }, val(pctClass(p), fmt(p, p % 1 ? 1 : 0) + '%'), sub(note));
    }
    function speedCell(events, per100) {
        const n = num(events);
        if (n === null) return dash();
        return h('td', { class: 'num' }, val(n > 0 ? 'is-bad' : 'is-good', fmt(n) + ' անգամ'),
            num(per100) !== null ? sub(fmt(per100, 2) + ' / 100 կմ') : null);
    }
    function stopCell(min, perDay) {
        const m = num(min);
        if (m === null) return dash();
        return h('td', { class: 'num' }, val(m > 0 ? 'is-warn' : 'is-good', fmt(m) + ' ր'),
            num(perDay) !== null ? sub(fmt(perDay) + ' ր/օր') : null);
    }
    function fuelCell(pct, fact, norm) {
        const p = num(pct);
        if (p === null) return dash();
        return h('td', { class: 'num' }, val(p > 10 ? 'is-bad' : p > 5 ? 'is-warn' : 'is-good', signed(p, 1) + '%'),
            num(norm) !== null ? sub(fmt(fact) + ' / ' + fmt(norm) + ' լ') : null);
    }
    const prCell = (p, r) => h('td', { class: 'num' + (p + r ? '' : ' is-mute') }, val(p + r ? 'is-warn' : '', fmt(p) + ' / ' + fmt(r)));
    const kmCell = (km) => (num(km) === null ? dash() : h('td', { class: 'num' }, val('', fmt(km))));
    const plainCell = (v, note) => h('td', { class: 'num' }, val('', fmt(v)), sub(note));
    // место по-армянски: 1-ին, 2-րդ, 3-րդ … — среди своей роли (водители и առաքիչ — раздельно)
    const place = (n, role) => fmt(n) + (num(n) === 1 ? '-ին' : '-րդ') + ' '
        + fmt(role === 'helper' ? state.data.ranked_helpers : state.data.ranked) + '-ից';
    // инициалы для кружка у имени: «Հարությունյան Արմեն» → «ՀԱ»
    const initials = (name) => String(name || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();

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
        if (!r.enough_data) { td.append(h('span', { class: 'dr-few', text: 'քիչ տվյալ' })); return td; }
        td.append(h('span', { class: 'dr-score-val ' + scoreClass(s), text: s === null ? '—' : fmt(s) }));
        if (s !== null) td.append(h('span', { class: 'dr-meter', 'aria-hidden': 'true' }, bar(s)));
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
        document.querySelectorAll('#drTable thead th[data-key]').forEach(th => {
            if (th.dataset.key === state.sort.key) th.setAttribute('aria-sort', state.sort.dir > 0 ? 'ascending' : 'descending');
            else th.removeAttribute('aria-sort');
        });
    }

    // --- раскрытая строка: балл, дни (строками той же таблицы), опоздавшие магазины ---
    function bar(score) {
        const i = h('i', { class: scoreClass(score) });
        i.style.width = Math.max(0, Math.min(100, score || 0)) + '%';
        return i;
    }
    function partsBlock(r) {
        const items = Object.entries(r.parts || {});
        const head = !r.enough_data ? scoreTitle(r) + '։ Ցուցանիշները՝ տեղեկության համար։'
            : 'Միավորի բաղադրիչները՝ ' + fmt(r.score) + (num(r.rank) !== null ? ' (' + place(r.rank, r.role) + ')' : '');
        // «Երթուղի» (08.10) — в балле (scorecard.WEIGHTS): здесь — по скольким дням и км он посчитан
        const route = num(r.route_pct) === null ? null : h('p', { class: 'dr-parts-note', text: 'Երթուղուն հետևում՝ '
            + fmt(r.route_pct, 1) + '% (' + fmt(r.route_days) + ' օր, ' + fmt(r.route_km) + ' կմ)' });
        return h('div', { class: 'dr-parts' }, h('p', { class: 'dr-parts-head', text: head }),
            items.length ? h('ul', {}, items.map(([k, p]) => h('li', {},
                h('span', { class: 'l', text: (PART[k] || [k])[0] }),
                h('span', { class: 'v', text: fmt(p.value, k === 'speed' ? 2 : 1) + (PART[k] || ['', ''])[1] }),
                h('span', { class: 'bar', 'aria-hidden': 'true' }, bar(num(p.score))),
                h('span', { class: 's', text: fmt(p.score) + ' միավոր × ' + fmt(p.share, 1) + '%' }))))
                : h('p', { class: 'is-mute', text: 'Ցուցանիշների տվյալ չկա (GPS և պլան չկան)։' }), route);
    }
    function dayLabel(ds) {
        if (typeof ds !== 'string' || ds.length !== 10) return '—';
        return WEEKDAY[parseIso(ds).getDay()] + ' ' + ds.slice(8, 10) + '.' + ds.slice(5, 7);
    }
    // день — строка в тех же столбцах, что и водитель (балл за день не считается)
    function dayRow(d, owner) {
        return h('tr', { class: 'dr-day', 'data-owner': owner },
            h('th', { scope: 'row', class: 'dr-day-name' }, h('span', { class: 'dr-day-date', text: dayLabel(d.date) }),
                h('span', { class: 'dr-day-car', text: (d.cars || []).join(', ') || '—' })),
            h('td', { class: 'num' }),
            plainCell(d.stops),
            onTimeCell(d.on_time_pct, d.on_time, d.rated, d.late_mean_min),
            pctCell(d.order_pct, d.ordered ? fmt(d.ordered) + ' խանութից' : null),
            pctCell(d.route_pct, null),
            kmCell(d.km),
            speedCell(d.speed_events, null),
            stopCell(d.offroute_min, null),
            fuelCell(d.liters_vs_norm_pct, d.fuel_fact_l, d.fuel_norm_l),
            prCell(d.partial, d.refused),
            state.cash ? cashCell(d.cash) : null,
            plainCell(d.tare));
    }
    function lateBlock(r) {
        const days = r.detail.filter(d => d.late && d.late.length);
        if (!days.length) return null;
        const total = days.reduce((n, d) => n + d.late.length, 0);
        const list = h('div', { class: 'dr-late-days' }, days.map(d => h('section', { class: 'dr-late-day' },
            h('h4', { text: dayLabel(d.date) + ' · ' + ((d.cars || []).join(', ') || '—') }),
            h('ul', { class: 'dr-late' }, d.late.map(x => h('li', {},
                h('span', { class: 'n', text: x.name || 'Խանութ' }),
                h('span', { class: 't', text: (x.planned || '—') + ' → ' + (x.arrive || '—') }),
                h('span', { class: 'd' + (x.reason === 'early' ? ' is-early' : ''),
                    text: (x.reason === 'early' ? '' : '+' + fmt(x.delay_min) + ' ր, ') + (REASON[x.reason] || '') })))))));
        // длинный список — свёрнут, короткий — сразу виден
        const box = h('details', { class: 'dr-late-box' }, h('summary', {},
            icon('fa-clock'), h('span', { text: 'Ուշացած խանութներ՝ ' + fmt(total) + ' (' + fmt(days.length) + ' օր)' })), list);
        box.open = total <= 12;
        return box;
    }

    function toggle(key) {
        if (state.open.has(key)) state.open.delete(key); else state.open.add(key);
        const row = document.querySelector('.dr-row[data-key="' + CSS.escape(key) + '"]');
        if (!row) return;
        const open = state.open.has(key);
        row.classList.toggle('is-open', open);
        row.querySelector('.dr-name').setAttribute('aria-expanded', String(open));
        document.querySelectorAll('#drRows [data-owner="' + CSS.escape(key) + '"]').forEach(x => { x.hidden = !open; });
    }

    // --- таблица ---
    function groupRow(text, n) {
        return h('tr', { class: 'dr-group' }, h('th', { scope: 'rowgroup', colspan: cols() },
            h('span', { text }), h('span', { class: 'dr-group-n', text: fmt(n) })));
    }
    function renderRows() {
        const body = $('drRows');
        const rows = state.data ? state.data.drivers : [];
        markSort();
        if (!rows.length) {
            body.replaceChildren(h('tr', {}, h('td', { colspan: cols(), class: 'rt-empty', text: 'Այս ժամանակահատվածում «Առաքիչ» հավելվածի նշումներ չկան։' })));
            return;
        }
        const out = [];
        const list = sorted(rows);
        const helpers = list.filter(r => r.role === 'helper').length;
        if (helpers) out.push(groupRow('Վարորդներ', list.length - helpers));
        list.forEach((r, i) => {
            if (helpers && r.role === 'helper' && (i === 0 || list[i - 1].role !== 'helper')) out.push(groupRow('Առաքիչներ', helpers));
            const open = state.open.has(r.key);
            const id = 'drDetail' + i;
            const name = h('button', { type: 'button', class: 'dr-name', 'aria-expanded': String(open), 'aria-controls': id },
                icon('fa-chevron-right'),
                h('i', { class: 'dr-ava ' + (r.enough_data ? scoreClass(num(r.score)) : 'is-mute'), 'aria-hidden': 'true', text: initials(r.name) }),
                h('b', { class: 'dr-who' }, h('span', { text: r.name }),
                    ROLE[r.role] ? h('em', { class: 'rt-badge b-manual', text: ROLE[r.role] }) : null));
            const tr = h('tr', { class: 'dr-row' + (open ? ' is-open' : '') + (num(r.stops) ? '' : ' is-idle'), 'data-key': r.key },
                h('th', { scope: 'row', class: 'dr-cell-name' }, name),
                scoreCell(r),
                plainCell(r.stops, fmt(r.days) + ' օր'),
                onTimeCell(r.on_time_pct, r.on_time, r.rated, r.late_mean_min),
                pctCell(r.order_pct, r.ordered ? fmt(r.ordered) + ' խանութից' : null),
                pctCell(r.route_pct, num(r.route_days) ? fmt(r.route_days) + ' օր' : null),
                kmCell(r.km),
                speedCell(r.speed_events, r.speed_per_100km),
                stopCell(r.offroute_min, r.offroute_min_per_day),
                fuelCell(r.liters_vs_norm_pct, r.fuel_fact_l, r.fuel_norm_l),
                prCell(r.partial, r.refused),
                state.cash ? cashCell(r.cash) : null,
                plainCell(r.tare));
            const detail = h('tr', { class: 'dr-detail', id, 'data-owner': r.key }, h('td', { colspan: cols() }, partsBlock(r)));
            const days = r.detail.length ? [h('tr', { class: 'dr-day-head', 'data-owner': r.key },
                h('td', { colspan: cols(), text: 'Ըստ օրերի՝ ' + fmt(r.detail.length) }))].concat(r.detail.map(d => dayRow(d, r.key))) : [];
            const late = lateBlock(r);
            const extra = late ? [h('tr', { class: 'dr-extra', 'data-owner': r.key }, h('td', { colspan: cols() }, late))] : [];
            [detail, ...days, ...extra].forEach(x => { x.hidden = !open; });
            out.push(tr, detail, ...days, ...extra);
        });
        body.replaceChildren(...out);
    }

    const kpi = (label, val, sub) => h('div', { class: 'dr-kpi' }, h('div', { class: 'dr-kpi-label', text: label }),
        h('div', { class: 'dr-kpi-val' }, val), sub ? h('div', { class: 'dr-kpi-sub', text: sub }) : null);

    function renderEta(e) {
        const ok = num(e && e.ok_min);
        $('drEtaBox').hidden = false;
        if (!e || !e.n) {
            $('drEta').replaceChildren(kpi('Գնահատված խանութներ', '—', 'պլանի ժամ և GPS-ով ժամանում չկա'));
            return;
        }
        const within = num(e.within_pct);
        $('drEta').replaceChildren(
            kpi('Պլանի ժամին ±' + fmt(ok) + ' ր', h('span', { class: pctClass(within), text: fmt(within, 1) + '%' }),
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
                rated ? fmt(ok) + '/' + fmt(rated) + ' խանութ' : null),
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
        $('drTableTitleText').textContent = 'Վարկանիշ՝ ' + dmy(d.from) + ' – ' + dmy(d.to);
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
        document.querySelectorAll('#drTable thead th[data-key]').forEach(th => th.querySelector('.dr-sort').addEventListener('click', () => {
            const key = th.dataset.key;
            // по умолчанию — «лучше сверху»: меньше опозданий, превышений, стоянок и перерасхода
            const asc = ['name', 'late_mean_min', 'speed_per_100km', 'offroute_min', 'liters_vs_norm_pct'].includes(key);
            state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: asc ? 1 : -1 };
            renderRows();
        }));
        // строка целиком раскрывает разбивку (кнопка имени — для клавиатуры и экранного диктора)
        // раскрытые блоки (разбивка балла, опоздания) — шириной с видимую часть таблицы, а не с всю таблицу
        const scroll = document.querySelector('.dr-scroll');
        const fit = () => $('drTable').style.setProperty('--dr-vw', scroll.clientWidth + 'px');
        if (window.ResizeObserver) new ResizeObserver(fit).observe(scroll);
        fit();
        $('drRows').addEventListener('click', (ev) => {
            const row = ev.target.closest('.dr-row');
            if (row) toggle(row.dataset.key);
        });
        load(null, null);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();

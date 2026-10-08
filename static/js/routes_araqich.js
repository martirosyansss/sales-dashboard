/* «Առաքիչների KPI» /routes/araqich — балл առաքիչ по выполнению плана (№88: route_optimizer/scorecard.py, роль helper) и
   объём по ERP за месяц и тренд за 6 месяцев без оценки (route_optimizer/crew_kpi.py): объём дня решает логист.
   API: GET /api/routes/araqich?month=YYYY-MM (объём ERP, месяцы для выбора), GET /api/routes/drivers/scorecard?from=&to=
   (балл по плану). Только администратору.
   Всё, что пришло с сервера, выводится только через textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const SVG = 'http://www.w3.org/2000/svg';
    const ok = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));
    const fmt = (v, d = 0) => !ok(v) ? '—'
        : Number(v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
    const pct = (v) => ok(v) ? fmt(v * 100) + '%' : '—';
    const MONTHS = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    const SHORT = ['հնվ', 'փտվ', 'մրտ', 'ապր', 'մյս', 'հնս', 'հլս', 'օգս', 'սեպ', 'հոկ', 'նոյ', 'դեկ'];
    const monthHy = (key) => MONTHS[+key.slice(5, 7) - 1] + ' ' + key.slice(0, 4);
    const TONE_FROM = 3;   // % (у нормы — процентных пунктов): меньшее изменение к прошлому месяцу — без цвета и стрелки

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
    function s(tag, attrs, text) {
        const el = document.createElementNS(SVG, tag);
        Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, String(v)));
        if (text !== undefined) el.textContent = text;
        return el;
    }

    const state = { data: null, seq: 0, planSeq: 0, sort: { key: 'points_day', dir: -1 }, open: null };

    function announce(text) { $('kpStatus').textContent = ''; setTimeout(() => { $('kpStatus').textContent = text; }, 30); }
    function showError(text) { $('kpAlert').hidden = !text; $('kpAlertText').textContent = text || ''; }

    function httpError(status, body) {
        const text = body && typeof body.error === 'string' ? body.error : '';
        if (status === 403) return 'Այս էջը ձեզ թույլատրված չէ։';
        if (status === 400 && /[Ա-֏]/.test(text)) return text;
        if (status === 503) return 'ERP տվյալների բազան հասանելի չէ, ցուցանիշները հնարավոր չէ հաշվել։ Կրկնեք մի փոքր ուշ։';
        if (status === 500 && /[Ա-֏]/.test(text)) return text;
        return 'Սերվերի սխալ (' + status + ')։ Կրկնեք մի փոքր ուշ։';
    }
    async function api(url) {
        let resp, body = null;
        try {
            resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
        } catch (e) { throw new Error('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
        if (resp.status === 401) {
            window.location.assign('/login?next=' + encodeURIComponent('/routes/araqich'));
            throw new Error('Մուտք գործեք նորից։');
        }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) throw new Error(httpError(resp.status, body));
        return body;
    }

    // ---------- загрузка месяца ----------
    async function load(month) {
        const seq = ++state.seq;          // ответ на прежний выбор месяца не перетирает новый
        showError('');
        $('kpLoading').hidden = false;
        $('kpTeam').hidden = true;
        $('kpTableCard').hidden = true;
        closePerson();
        try {
            const data = await api('/api/routes/araqich' + (month ? '?month=' + encodeURIComponent(month) : ''));
            if (seq !== state.seq) return;
            state.data = data;
            render();
            loadPlan(data.month, data.current);
            announce('Ցուցանիշները հաշվված են՝ ' + monthHy(data.month));
        } catch (e) {
            if (seq !== state.seq) return;
            state.data = null;
            showError(e.message);
        } finally {
            if (seq === state.seq) $('kpLoading').hidden = true;
        }
    }

    function render() {
        const d = state.data;
        $('kpMonth').replaceChildren(...d.months.map((key, i) => h('option', {
            value: key, text: monthHy(key) + (i === 0 ? ' (մինչև այսօր)' : ''),
        })));
        $('kpMonth').value = d.month;
        $('kpNorm').textContent = fmt(d.norm_per_day, d.norm_per_day % 1 ? 1 : 0);
        $('kpSub').textContent = monthHy(d.month) + (d.current ? ' (մինչև այսօր)' : '') + ' · աշխատանքային օրեր՝ '
            + d.team.workdays + ' · առաքիչներ՝ ' + d.team.people;
        const warn = [
            d.calendar_warning || '',
            d.current && d.team.workdays < d.min_days ? 'Ամսվա սկիզբն է՝ ' + d.team.workdays + ' աշխատանքային օր․ '
                + 'միավորը կերևա ' + d.min_days + '-րդ օրից։ Ամբողջական պատկերի համար ընտրեք նախորդ ամիսը։' : '',
        ].filter(Boolean);
        $('kpWarn').hidden = !warn.length;
        $('kpWarnText').textContent = warn.join(' ');
        renderTeam(d.team, d.prev_team);
        renderTable();
        $('kpTeam').hidden = false;
        $('kpTableCard').hidden = false;
    }

    // ---------- команда ----------
    // better: +1 — рост хорошо, −1 — рост плохо (֏ за тонну)
    function tile(label, now, prev, digits, unit, better, asPct) {
        const show = (v) => asPct ? fmt(v * 100) : fmt(v, digits);
        let tone = '', delta = null;
        if (ok(now) && ok(prev) && prev !== 0) {
            const ch = asPct ? (now - prev) * 100 : (now - prev) / Math.abs(prev) * 100;
            const r = Math.round(ch);
            if (better && Math.abs(ch) >= TONE_FROM) tone = (r > 0) === (better > 0) ? 'good' : 'bad';
            delta = (r > 0 ? '▲ +' : r < 0 ? '▼ −' : '') + fmt(Math.abs(r)) + (asPct ? ' տոկոսային կետ' : '%') + ' նախորդ ամսվա համեմատ';
        }
        return h('div', { class: 'rt-kpi' + (tone ? ' is-' + tone : '') },
            h('div', { class: 'rt-kpi-label', text: label }),
            h('div', { class: 'rt-kpi-val' }, h('span', { class: 'now', text: show(now) }),
                unit ? h('span', { class: 'unit', text: unit }) : null),
            delta ? h('span', { class: 'rt-kpi-delta', text: delta }) : null,
            h('div', { class: 'rt-kpi-sub' }, 'Նախորդ ամիս՝ ', h('b', { text: ok(prev) ? show(prev) + (unit ? ' ' + unit : '') : '—' })));
    }
    function renderTeam(t, p) {
        p = p || {};
        $('kpTeam').replaceChildren(
            tile('Կետ/օր՝ թիմի մեդիան', t.median_points_day, p.median_points_day, 1, 'կետ', 0),
            tile('Տոննա/օր՝ մեկ առաքիչի հաշվով', t.tonnes_day, p.tonnes_day, 2, 'տ', 0),
            tile('Կետեր՝ «Աշխատավարձ»-ի նորմից', t.norm, p.norm, 0, '%', 0, true),
            tile('Վարձը մեկ տոննայի համար', t.cost_tonne, p.cost_tonne, 0, '֏', -1));
    }

    // ---------- таблица ----------
    // get — значение для сортировки и показа; prev — сравнение с прошлым месяцем (стрелка); better как у плиток
    const COLS = [
        { key: 'name', label: 'Առաքիչ', get: p => p.name, text: true },
        { key: 'days', label: 'Օրեր', get: p => p.now.days },
        { key: 'points_day', label: 'Կետ/օր', get: p => p.now.points_day, d: 1, prev: p => p.prev && p.prev.points_day, better: 0 },
        { key: 'tonnes_day', label: 'Տոննա/օր', get: p => p.now.tonnes_day, d: 2, prev: p => p.prev && p.prev.tonnes_day, better: 0 },
        { key: 'kg_point', label: 'Կգ/կետ', get: p => p.now.kg_point, d: 0 },
        { key: 'sales_day', label: 'Վաճառք/օր, ֏', get: p => p.now.sales_day, d: 0 },
        { key: 'norm', label: 'Նորմ', get: p => p.now.norm, pct: true },
        { key: 'cost_tonne', label: '֏/տոննա', get: p => p.now.cost_tonne, d: 0, prev: p => p.prev && p.prev.cost_tonne, better: -1 },
        { key: 'trend', label: '6 ամիս', none: true },
    ];

    function sortedPeople() {
        const { key, dir } = state.sort;
        const col = COLS.find(c => c.key === key);
        return state.data.people.slice().sort((a, b) => {
            // «քիչ օր» — всегда внизу: 1–2 дня не сравнимы с полным месяцем (кроме сортировки по имени)
            const few = (a.grade === 'fewdays') - (b.grade === 'fewdays');
            if (few && !col.text) return few;
            const x = col.get(a), y = col.get(b);
            if (col.text) return dir * String(x).localeCompare(String(y), 'hy');
            if (!ok(x) && !ok(y)) return 0;
            if (!ok(x)) return 1;           // «—» всегда внизу
            if (!ok(y)) return -1;
            return dir * (x - y);
        });
    }

    function renderHead() {
        $('kpHead').replaceChildren(h('tr', null, COLS.map(c => {
            if (c.none) return h('th', { scope: 'col', class: 'kp-spark-h', text: c.label });
            const on = state.sort.key === c.key;
            const btn = h('button', { type: 'button', class: 'kp-sort' + (on ? ' is-on' : ''), 'data-key': c.key },
                c.label, on ? h('i', { class: 'fas fa-arrow-' + (state.sort.dir < 0 ? 'down' : 'up'), 'aria-hidden': 'true' }) : null);
            return h('th', { scope: 'col', class: c.text ? 'kp-name' : null,
                'aria-sort': on ? (state.sort.dir < 0 ? 'descending' : 'ascending') : null }, btn);
        })));
    }

    function arrow(now, prev, better) {
        if (!ok(now) || !ok(prev) || prev === 0) return null;
        const ch = (now - prev) / Math.abs(prev);
        if (Math.abs(ch) * 100 < TONE_FROM) return null;    // как у плиток: меньше — шум месяца, без стрелки
        const good = (ch > 0) === (better > 0);
        return h('span', { class: 'kp-arr' + (better ? (good ? ' is-good' : ' is-bad') : ''),
            title: 'Նախորդ ամիս՝ ' + fmt(prev, Math.abs(prev) < 10 ? 2 : 0) },
            (ch > 0 ? '▲' : '▼') + fmt(Math.abs(ch) * 100) + '%');
    }

    function spark(trend) {
        const w = 96, hgt = 26, pad = 3;
        const vals = trend.filter(ok);
        const svg = s('svg', { viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, class: 'kp-spark', 'aria-hidden': 'true' });
        if (!vals.length) return svg;
        const max = Math.max(...vals), min = Math.min(...vals);
        const x = (i) => pad + i * (w - 2 * pad) / Math.max(1, trend.length - 1);
        const y = (v) => max === min ? hgt / 2 : hgt - pad - (v - min) / (max - min) * (hgt - 2 * pad);
        let seg = [];
        const flush = () => {
            if (seg.length > 1) svg.append(s('polyline', { points: seg.join(' '), class: 'kp-spark-line' }));
            seg = [];
        };
        trend.forEach((v, i) => {
            if (!ok(v)) { flush(); return; }
            seg.push(x(i).toFixed(1) + ',' + y(v).toFixed(1));
            svg.append(s('circle', { cx: x(i).toFixed(1), cy: y(v).toFixed(1), r: i === trend.length - 1 ? 2.6 : 1.6,
                class: i === trend.length - 1 ? 'kp-spark-now' : 'kp-spark-dot' }));
        });
        flush();
        return svg;
    }

    function cell(c, p) {
        if (c.key === 'name') {
            return h('td', { class: 'txt kp-name' }, h('button', { type: 'button', class: 'rt-linkbtn kp-person', text: p.name,
                'aria-label': p.name + '՝ օր առ օր', 'aria-controls': 'kpPerson',
                'aria-expanded': state.open === p.code + '|' + p.name ? 'true' : 'false' }),
                p.grade === 'fewdays' ? h('span', { class: 'rt-badge b-none', text: 'քիչ օր' }) : null,
                h('span', { class: 'kp-code', text: p.code }));
        }
        if (c.key === 'days') {
            return h('td', { class: 'num' }, String(p.now.days),
                h('span', { class: 'kp-dim', text: ' · ' + pct(p.now.attendance) }));
        }
        if (c.key === 'trend') {
            const t = p.trend.map(v => ok(v) ? fmt(v, 1) : '—').join(' → ');
            return h('td', { class: 'kp-spark-cell', title: 'Կետ/օր՝ ' + t }, spark(p.trend),
                h('span', { class: 'rt-sr-only', text: 'Կետ/օր՝ ' + t }));
        }
        const v = c.get(p);
        return h('td', { class: 'num' }, c.pct ? pct(v) : fmt(v, c.d),
            c.prev ? arrow(v, c.prev(p), c.better) : null);
    }

    function renderTable() {
        renderHead();
        const people = sortedPeople();
        $('kpEmpty').hidden = people.length > 0;
        $('kpTableWrap').hidden = !people.length;
        $('kpRows').replaceChildren(...people.map(p => {
            const tr = h('tr', { 'data-key': p.code + '|' + p.name, class: state.open === p.code + '|' + p.name ? 'is-open' : null },
                COLS.map(c => cell(c, p)));
            tr.addEventListener('click', () => openPerson(p));   // кнопка имени — клавиатура и чтец, строка — мышь
            return tr;
        }));
    }

    // ---------- балл по выполнению плана (№88) ----------
    const PLAN_PART = { clean: 'Առանց խնդրի', on_time: 'Ժամանակին', unload: 'Բեռնաթափում՝ նորմի', day: 'Օրը՝ պլանի' };
    const PCT = (v, cls) => h('td', { class: 'num' + (cls ? ' ' + cls : '') }, ok(v) ? fmt(v, 1) + '%' : '—');
    // разгрузка и день: 100 % — как норма / план; больше — дольше
    const slow = (v, warn, bad) => !ok(v) ? '' : v >= bad ? 'is-bad' : v >= warn ? 'is-warn' : 'is-good';
    const high = (v, warn, bad) => !ok(v) ? '' : v <= bad ? 'is-bad' : v <= warn ? 'is-warn' : 'is-good';

    async function loadPlan(month, current) {
        const seq = ++state.planSeq;
        $('kpPlanLoading').hidden = false;
        $('kpPlanWrap').hidden = true;
        $('kpPlanEmpty').hidden = true;
        $('kpPlanNote').textContent = '';
        $('kpPlanSub').textContent = '';
        const y = +month.slice(0, 4), m = +month.slice(5, 7);
        const last = new Date(y, m, 0).getDate();
        // текущий месяц — по сегодня (сервер, дата Еревана); прошлый — до последнего дня
        const q = '?from=' + month + '-01' + (current ? '' : '&to=' + month + '-' + String(last).padStart(2, '0'));
        try {
            const d = await api('/api/routes/drivers/scorecard' + q);
            if (seq !== state.planSeq) return;
            renderPlan(d);
        } catch (e) {
            if (seq !== state.planSeq) return;
            $('kpPlanNote').textContent = e.message;
        } finally {
            if (seq === state.planSeq) $('kpPlanLoading').hidden = true;
        }
    }

    function renderPlan(d) {
        const rows = d.drivers.filter(r => r.role === 'helper').sort((a, b) =>
            (b.score ?? -1) - (a.score ?? -1) || b.stops - a.stops || a.name.localeCompare(b.name, 'hy'));
        const w = (d.rules && d.rules.helper_weights) || {};
        $('kpWeights').textContent = 'առանց խնդրի ' + fmt(w.clean) + ', ժամանակին ' + fmt(w.on_time)
            + ', բեռնաթափում ' + fmt(w.unload) + ', օր ' + fmt(w.day);
        $('kpPlanMinDays').textContent = fmt(d.rules && d.rules.min_days);
        $('kpPlanSub').textContent = 'առաքիչներ՝ ' + rows.length + (d.ranked_helpers ? ' · միավորով՝ ' + d.ranked_helpers : '');
        $('kpPlanEmpty').hidden = rows.length > 0;
        $('kpPlanWrap').hidden = !rows.length;
        $('kpPlanRows').replaceChildren(...rows.map(r => {
            const s = ok(r.score) ? r.score : null;
            const scoreTd = h('td', { class: 'num kp-score',
                title: Object.entries(r.parts || {}).map(([k, p]) => (PLAN_PART[k] || k) + '՝ ' + fmt(p.value, 1) + '% → ' + fmt(p.score) + ' միավոր').join('\n') || null },
                s === null ? h('span', { class: 'rt-badge b-none', text: r.enough_data ? '—' : 'քիչ տվյալ' })
                    : h('span', { class: 'kp-score-val ' + (s >= 80 ? 'is-good' : s >= 60 ? 'is-warn' : 'is-bad'), text: fmt(s) }),
                ok(r.rank) ? h('small', { text: fmt(r.rank) + (r.rank === 1 ? '-ին' : '-րդ') + ' ' + fmt(d.ranked_helpers) + '-ից' }) : null);
            return h('tr', null,
                h('td', { class: 'txt kp-name' }, h('span', { class: 'kp-person-name', text: r.name })),
                scoreTd,
                h('td', { class: 'num', text: fmt(r.days) }),
                h('td', { class: 'num', text: fmt(r.stops) }),
                PCT(r.clean_pct, high(r.clean_pct, 95, 85)),
                PCT(r.on_time_pct, high(r.on_time_pct, 85, 60)),
                PCT(r.unload_vs_norm_pct, slow(r.unload_vs_norm_pct, 115, 140)),
                PCT(r.day_vs_plan_pct, slow(r.day_vs_plan_pct, 110, 125)));
        }));
        const c = d.coverage || {};
        const note = [];
        if (rows.length) note.push('Մանրամասները՝ «Վարորդներ» էջում։');
        if (!d.gps) note.push('GPS-ը միացված չէ․ «Ժամանակին»-ը, բեռնաթափումը և օրը չեն հաշվվում։');
        else if (c.closed) note.push('Ժամանակին-ը գնահատված է ' + fmt(c.rated) + ' խանութի համար ' + fmt(c.closed) + ' փակվածից։');
        $('kpPlanNote').textContent = note.join(' ');
    }

    // ---------- человек: дни и полгода ----------
    function bars(items, opts) {
        // items: [{label, value, title}]; opts: {norm, digits}
        const n = items.length, bw = 26, gap = 6, top = 16, hgt = 120, bottom = 22;
        const w = Math.max(n * (bw + gap) + gap, 160);
        const max = Math.max(opts.norm || 0, ...items.map(i => ok(i.value) ? i.value : 0), 1);
        const y = (v) => top + hgt - v / max * hgt;
        const svg = s('svg', { viewBox: `0 0 ${w} ${top + hgt + bottom}`, width: w, height: top + hgt + bottom,
            class: 'kp-bars', 'aria-hidden': 'true' });
        items.forEach((it, i) => {
            const x = gap + i * (bw + gap);
            if (ok(it.value)) {
                const below = opts.norm && it.value < opts.norm;
                const r = s('rect', { x, y: y(it.value).toFixed(1), width: bw, height: (top + hgt - y(it.value)).toFixed(1),
                    rx: 3, class: 'kp-bar' + (below ? ' is-below' : '') });
                r.append(s('title', null, it.title));
                svg.append(r, s('text', { x: x + bw / 2, y: (y(it.value) - 4).toFixed(1), class: 'kp-bar-v' },
                    fmt(it.value, opts.digits || 0)));
            }
            svg.append(s('text', { x: x + bw / 2, y: top + hgt + 15, class: 'kp-bar-l' }, it.label));
        });
        if (opts.norm) {
            svg.append(s('line', { x1: 0, x2: w, y1: y(opts.norm).toFixed(1), y2: y(opts.norm).toFixed(1), class: 'kp-norm' }));
        }
        return svg;
    }

    function openPerson(p) {
        const d = state.data;
        state.open = p.code + '|' + p.name;
        document.querySelectorAll('#kpRows tr').forEach(tr => {
            const on = tr.dataset.key === state.open;
            tr.classList.toggle('is-open', on);
            const btn = tr.querySelector('.kp-person');
            if (btn) btn.setAttribute('aria-expanded', on ? 'true' : 'false');
        });
        $('kpPersonTitle').textContent = p.name + ' · ' + monthHy(d.month);
        const days = p.by_day.map(x => ({ label: x.date.slice(8, 10), value: x.points,
            title: x.date.slice(8, 10) + '.' + x.date.slice(5, 7) + '՝ ' + x.points + ' կետ, ' + fmt(x.tonnes, 2) + ' տ' }));
        $('kpDays').replaceChildren(bars(days, { norm: d.norm_per_day }));
        $('kpDays').setAttribute('aria-label', 'Կետեր ըստ օրերի՝ ' + p.by_day.map(x => x.date.slice(8, 10) + '․ ' + x.points).join(', ')
            + '։ Գիծը՝ նորմը ' + fmt(d.norm_per_day) + ' կետ։');
        const trend = d.trend_months.map((m, i) => ({ label: SHORT[+m.slice(5, 7) - 1], value: p.trend[i],
            title: monthHy(m) + '՝ ' + (ok(p.trend[i]) ? fmt(p.trend[i], 1) + ' կետ/օր' : 'չի աշխատել') }));
        $('kpTrend').replaceChildren(bars(trend, { norm: d.norm_per_day, digits: 1 }));
        $('kpTrend').setAttribute('aria-label', 'Կետ/օր ըստ ամիսների՝ ' + trend.map(t => t.label + ' ' + (ok(t.value) ? fmt(t.value, 1) : '—')).join(', '));
        $('kpPerson').hidden = false;
        $('kpPerson').focus({ preventScroll: true });
        $('kpPerson').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    }
    function closePerson() {
        $('kpPerson').hidden = true;
        const was = state.open;
        state.open = null;
        document.querySelectorAll('#kpRows tr.is-open').forEach(tr => {
            tr.classList.remove('is-open');
            const btn = tr.querySelector('.kp-person');
            if (btn) btn.setAttribute('aria-expanded', 'false');
        });
        return was;
    }

    // ---------- события ----------
    let pick = 0;   // перебор месяцев стрелками шлёт change на каждое нажатие — читаем ERP только за последний выбор
    $('kpMonth').addEventListener('change', () => {
        clearTimeout(pick);
        pick = setTimeout(() => load($('kpMonth').value), 300);
    });
    $('kpHead').addEventListener('click', (e) => {
        const btn = e.target.closest('.kp-sort');
        if (!btn || !state.data) return;
        const key = btn.dataset.key;
        state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === 'name' || key === 'cost_tonne' ? 1 : -1 };
        renderTable();
        const again = $('kpHead').querySelector('.kp-sort[data-key="' + key + '"]');
        if (again) again.focus();
    });
    function closeAndFocus() {
        const was = closePerson();
        const row = was && Array.from(document.querySelectorAll('#kpRows tr')).find(tr => tr.dataset.key === was);
        const btn = row && row.querySelector('.kp-person');
        if (btn) btn.focus();
    }
    $('kpPersonClose').addEventListener('click', closeAndFocus);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('kpPerson').hidden) closeAndFocus(); });

    // до ответа сервера — 12 последних месяцев по часам компьютера, чтобы выбор не был пустым
    const now = new Date();
    $('kpMonth').replaceChildren(...Array.from({ length: 12 }, (_, i) => {
        const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
        const key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
        return h('option', { value: key, text: monthHy(key) + (i === 0 ? ' (մինչև այսօր)' : '') });
    }));
    load('');
})();

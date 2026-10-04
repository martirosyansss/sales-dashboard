/* «Маршруты · как сейчас» /routes — оценка текущего плана из ERP (этап 1).
   Данные: GET /api/routes/overview (контракт — docs/plans/stage-1-plan.md §10.1).
   Пороги (100 000 / 150 000) и длина рабочего дня — из GET /api/routes/settings (§10.2);
   если настройки не пришли, тексты обходятся без чисел порогов.
   Безопасность: всё, что пришло из ERP (имена, коды, группы, территории, машины), выводится
   только через esc() или textContent — в том числе в подсказках и попапах Leaflet. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    // Страница на армянском (решение владельца №58); словари — как в «Развозе» (docs/research/armenian-glossary.md)
    const WD_SHORT = { 1: 'Երկ', 2: 'Երք', 3: 'Չրք', 4: 'Հնգ', 5: 'Ուրբ', 6: 'Շբթ', 7: 'Կիր' };
    const WD_GEN = { 1: 'երկուշաբթի օրվա', 2: 'երեքշաբթի օրվա', 3: 'չորեքշաբթի օրվա', 4: 'հինգշաբթի օրվա', 5: 'ուրբաթ օրվա', 6: 'շաբաթ օրվա' };   // «երեքշաբթի օրվա պատվերները»
    const WD_FULL = { 1: 'երկուշաբթի', 2: 'երեքշաբթի', 3: 'չորեքշաբթի', 4: 'հինգշաբթի', 5: 'ուրբաթ', 6: 'շաբաթ', 7: 'կիրակի' };
    const MONTHS = ['հնվ', 'փտվ', 'մրտ', 'ապր', 'մյս', 'հնս', 'հլս', 'օգս', 'սեպ', 'հոկ', 'նոյ', 'դեկ'];
    const MONTHS_FULL = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    // Цвета менеджеров: фиксированный порядок 12 слотов. Соседние слоты различимы и при
    // дальтонизме (CVD ΔE ≥ 17, обычное зрение ΔE ≥ 17), контраст к тёмному фону ≥ 3:1.
    // Цвет закреплён за позицией менеджера в ответе API — фильтр не перекрашивает точки.
    const MGR_COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3',
                        '#0d9298', '#b8b90c', '#ae55c1', '#37981b', '#fe80c0', '#d64651'];
    const MGR_OTHER = '#8b93a7';   // 13-й и дальше — нейтральный серый, цвета не повторяем
    const SIZE = { small: 'փոքր', medium: 'միջին', large: 'խոշոր' };
    const COORD = {
        erp: ['b-erp', 'ERP', 'կոորդինատը՝ ERP-ի հասցեից'],
        gps: ['b-gps', 'GPS', 'ըստ մենեջերի այցերի GPS-ի'],
        driver: ['b-ok', 'վարորդներ', 'ըստ վարորդների GPS-ի՝ առաքման ժամանակ'],
        manual: ['b-manual', 'ձեռքով', 'նշված է ձեռքով'],
        none: ['b-none', 'չկա', 'կոորդինատ չկա — քարտեզում ցույց տրված չէ'],
    };
    const HOME_SRC = { manual: 'նշված է ձեռքով', gps_auto: 'գտնվել է GPS-ով՝ ավտոմատ', none: 'հայտնի չէ' };
    // Статус клиента по давности последнего заказа (§15): [класс бейджа, подпись]; active — без бейджа
    const STATUS = {
        dormant: ['b-warn', 'դադարել է գնել'], lost: ['b-danger', 'վաղուց չի գնում'], never: ['b-danger', 'վերջին տարում ոչ մի պատվեր'],
        new: ['b-ok', 'նոր'], seasonal: ['b-none', 'սեզոնային'],
    };
    const SILENT = new Set(['dormant', 'lost', 'never']);   // в ожидаемой выручке их нет (λ = 0)
    const RISK_PAGE = 30;                                  // строк «Клиентов под риском» до «Показать все»
    const NORM_SRC = { manual: 'նշված է ձեռքով', gps: 'ըստ GPS հետագծերի', default: 'լռելյայն' };
    const ORDER_NOTE = 'Այցելության հերթականությունը՝ օրվա ներսում ամենակարճը (այդպես են մենեջերներն իրականում շրջում ըստ GPS-ի)։ № ERP-ում՝ հերթականությունը ERP-ի երթուղում։';
    const P_SHORT = 0.5;      // «машин не хватает»: в пик рейсы не укладываются в день машин в половине проб и чаще
    const YEREVAN = [40.1792, 44.4991];
    const LS_KEY = 'routesMapFilter';
    const RM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---------- Форматирование ----------
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => {
        const n = num(v);
        return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d });
    };
    const NB = '\u00a0';   // неразрывный пробел: число не отрывается от единицы при переносе
    const money = (v) => num(v) === null ? '—' : fmt(Math.round(num(v))) + NB + 'դրամ';
    function moneyShort(v) {
        const n = num(v);
        if (n === null) return '—';
        const a = Math.abs(n);
        if (a >= 1e6) return fmt(n / 1e6, 1) + ' մլն';
        if (a >= 1e3) return fmt(Math.round(n / 1e3)) + ' հազ.';
        return fmt(Math.round(n));
    }
    const pct = (p) => num(p) === null ? '—' : Math.round(num(p) * 100) + '%';
    const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
    // «2026-07-02» → «02.07.2026»
    const dateRu = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    function hm(minutes) {
        const n = num(minutes);
        if (n === null) return '—';
        const m = Math.max(0, Math.round(n)), h = Math.floor(m / 60), r = m % 60;
        return h ? (r ? h + ' ժ ' + r + ' րոպե' : h + ' ժ') : r + ' րոպե';
    }
    // По-армянски существительное после числа — в единственном числе («5 այց», «1,6 երթ»): склонять по числу не нужно
    // Порядковое: «1-ին», «2-րդ» («2-րդ շաբաթ»)
    const ord = (n) => (Number(n) === 1 ? '1-ին' : n + '-րդ');
    // Ссылка из ответа сервера — только относительный путь этого же сайта
    const safeLink = (u) => (typeof u === 'string' && /^\/(?!\/)[^\s\\]*$/.test(u)) ? u : null;
    const mgrColor = (mi) => MGR_COLORS[mi] || MGR_OTHER;
    const setLink = (hash, text) => '<a href="/routes/settings#' + hash + '">' + text + '</a>';
    function parseTime(s) {
        if (typeof s !== 'string' || !s) return null;
        const t = new Date(s);
        return Number.isNaN(t.getTime()) ? null : t;
    }
    function workHours(s) {
        const p = (t) => { const m = /^(\d{1,2}):(\d{2})$/.exec(String(t || '')); return m ? (+m[1]) * 60 + (+m[2]) : null; };
        const a = p(s && s.work_start), b = p(s && s.work_end);
        return (a === null || b === null || b <= a) ? null : (b - a) / 60;
    }
    // Месяцы подряд — «январь – март» (в т.ч. через Новый год), иначе «янв, мар, дек»
    function monthsText(list) {
        const ms = [...new Set((list || []).map(Number).filter(m => m >= 1 && m <= 12))].sort((a, b) => a - b);
        if (!ms.length) return 'որոշված չեն';
        if (ms.length === 12) return 'ամբողջ տարին';
        if (ms.length === 1) return MONTHS_FULL[ms[0] - 1];
        // ровно один разрыв по кругу года — это один сплошной отрезок: он кончается перед разрывом
        const gaps = ms.filter((m, i) => ms[(i + 1) % ms.length] !== (m % 12) + 1);
        if (gaps.length === 1) {
            const end = gaps[0], start = ms[(ms.indexOf(end) + 1) % ms.length];
            return MONTHS_FULL[start - 1] + ' – ' + MONTHS_FULL[end - 1];
        }
        return ms.map(m => MONTHS[m - 1]).join(', ');
    }

    // ---------- Состояние ----------
    const state = {
        data: null, settings: null, idx: null,
        mgr: '', days: new Set(),     // фильтры карты: agent_id и дни недели (пусто = все)
        sel: null,                    // выбранный день: {agent, week, weekday}
        map: null, mapFailed: false, custLayer: null, routeLayer: null, baseLayer: null,
        loading: false, lastRefresh: false,
        riskSort: 'rev', riskAll: false,   // «Клиенты под риском»: сортировка и «Показать все»
    };

    // Липкое меню дашборда переносится на 2–3 строки — прокрутка к разделу учитывает его высоту
    function syncNavOffset() {
        const nav = document.querySelector('.navbar.sticky-top');
        const h = nav ? Math.ceil(nav.getBoundingClientRect().height) : 0;
        $('rtOverview').style.setProperty('--rt-nav-h', h + 'px');
        return h;
    }

    function announce(msg) {
        const el = $('rtStatus');
        el.textContent = '';
        setTimeout(() => { el.textContent = msg; }, 40);
    }

    function saveFilter() {
        try { localStorage.setItem(LS_KEY, JSON.stringify({ mgr: state.mgr, days: [...state.days] })); } catch (e) { /* приватный режим */ }
    }
    function loadFilter() {
        try {
            const f = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
            if (f && typeof f.mgr === 'string') state.mgr = f.mgr;
            if (f && Array.isArray(f.days)) state.days = new Set(f.days.map(Number).filter(n => n >= 1 && n <= 7));
        } catch (e) { /* нет доступа к localStorage */ }
    }

    // ---------- Загрузка ----------
    // Ответы не из раздела маршрутов (вход, доступ — тексты дашборда по-русски) — своим армянским текстом по коду
    // ответа (глоссарий §1.13, как routes_garage.js); армянский текст сервера показывается как есть
    const HY = /[\u0531-\u058F]/;
    const HTTP_TEXT = {
        400: 'Սերվերը չընդունեց հարցումը։', 401: 'Անհրաժեշտ է մուտք գործել համակարգ։', 403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։',
        404: 'Չի գտնվել։', 415: 'Սերվերը չընդունեց հարցումը։', 500: 'Սերվերի ներքին սխալ։', 503: 'ERP տվյալների բազան հասանելի չէ։',
    };
    async function fetchJSON(url) {
        let resp;
        try {
            resp = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store' });
        } catch (e) {
            throw Object.assign(new Error('Սերվերի հետ կապ չկա։ Ստուգեք ցանցը և սեղմեք «Կրկնել»։'), { network: true });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!data || typeof data !== 'object') {
            throw Object.assign(new Error('Սերվերն անհասկանալի պատասխան տվեց (կոդ ' + resp.status + ')։ Փորձեք կրկին։'), { status: resp.status });
        }
        if (!resp.ok || data.success !== true) {
            // 403 CSRF дашборда («сессия формы устарела») — не запрет доступа (как routes_learning.js и routes_garage.js)
            if (resp.status === 403 && data.error === 'csrf') throw Object.assign(new Error('Էջը հնացել է՝ թարմացրեք այն և կրկնեք։'), { status: resp.status });
            const text = typeof data.error === 'string' && HY.test(data.error) ? data.error : '';
            throw Object.assign(new Error(text || HTTP_TEXT[resp.status] || ('Սերվերի սխալ (կոդ ' + resp.status + ')։')), { status: resp.status });
        }
        return data;
    }

    function setBusy(on) {
        const btn = $('rtRefreshBtn');
        btn.disabled = on;
        btn.setAttribute('aria-busy', on ? 'true' : 'false');
        btn.querySelector('i').className = on ? 'rt-spin-inline' : 'fas fa-rotate';
        btn.querySelector('span').textContent = on ? 'Հաշվում եմ…' : 'Թարմացնել';
    }

    async function load(refresh) {
        if (state.loading) return;
        state.loading = true;
        state.lastRefresh = !!refresh;
        const first = !state.data;
        setBusy(true);
        $('rtError').classList.add('d-none');
        if (first) $('rtLoading').classList.remove('d-none');
        else announce('Կրկին կարդում եմ տվյալները ERP-ից…');
        try {
            const [ov, st] = await Promise.allSettled([
                fetchJSON('/api/routes/overview' + (refresh ? '?refresh=1' : '')),
                fetchJSON('/api/routes/settings'),
            ]);
            if (ov.status === 'rejected') throw ov.reason;
            state.settings = (st.status === 'fulfilled' && st.value.settings) ? st.value.settings : null;
            setData(ov.value);
            const stale = state.data.warnings.some(w => w && w.code === 'erp_stale');
            if (!first) announce(stale ? 'ERP-ն հասանելի չէ — էկրանին նախկին տվյալներն են' : 'Տվյալները թարմացված են');
        } catch (e) {
            showError(e, !first);
        } finally {
            state.loading = false;
            setBusy(false);
            $('rtLoading').classList.add('d-none');
        }
    }

    function showError(e, keepOld) {
        const auth = e && e.status === 401;
        $('rtErrorText').textContent = (keepOld ? 'Չհաջողվեց թարմացնել — էկրանին նախկին տվյալներն են։ ' : 'Չհաջողվեց բեռնել երթուղիները։ ')
            + ((e && e.message) || 'Անհայտ սխալ։');
        $('rtLoginLink').classList.toggle('d-none', !auth);
        $('rtRetryBtn').classList.toggle('d-none', auth);
        $('rtError').classList.remove('d-none');
    }

    function setData(d) {
        // Защитно приводим к ожидаемым типам то, что обходим циклами
        d.managers = Array.isArray(d.managers) ? d.managers : [];
        d.managers.forEach(m => {
            m.days = Array.isArray(m.days) ? m.days : [];
            m.days.forEach(day => { day.stops = Array.isArray(day.stops) ? day.stops : []; });
        });
        d.customers = (d.customers && typeof d.customers === 'object') ? d.customers : {};
        d.at_risk_customers = Array.isArray(d.at_risk_customers)
            ? d.at_risk_customers.filter(x => x && typeof x === 'object') : [];
        d.totals = (d.totals && typeof d.totals === 'object') ? d.totals : {};
        d.warnings = Array.isArray(d.warnings) ? d.warnings : [];
        d.norms = (d.norms && typeof d.norms === 'object') ? d.norms : {};
        state.data = d;
        state.idx = buildIndex(d);
        if (state.mgr && !d.managers.some(m => String(m.agent_id) === state.mgr)) state.mgr = '';
        render();
    }

    // Индексы: клиент → визиты плана; колонки тепловой таблицы; рабочие дни
    function buildIndex(d) {
        const byCust = new Map();
        let maxWeek = 1;
        const wds = new Set((Array.isArray(d.weekdays) ? d.weekdays : []).map(Number));
        d.managers.forEach((m, mi) => {
            m._byKey = new Map();
            m.days.forEach((day, di) => {
                const w = Number(day.week) || 1, wd = Number(day.weekday);
                maxWeek = Math.max(maxWeek, w);
                wds.add(wd);
                m._byKey.set(w + '-' + wd, di);
                (Array.isArray(day.customer_ids) ? day.customer_ids : []).forEach(id => {
                    const k = String(id);
                    let arr = byCust.get(k);
                    if (!arr) byCust.set(k, arr = []);
                    arr.push({ mi, di });
                });
            });
        });
        const W = Math.max(1, Number(d.cycle_weeks) || 1, maxWeek);
        const wdList = [...wds].filter(w => w >= 1 && w <= 7).sort((a, b) => a - b);
        const cols = [];
        for (let k = 1; k <= W; k++) wdList.forEach(w => cols.push({ week: k, weekday: w }));
        // Рабочие дни недели: из настроек, иначе — дни плана без пометки off_day.
        // weekdays из ответа для этого не годится: это рабочие дни ∪ дни, что есть в плане.
        const sw = state.settings && Array.isArray(state.settings.workdays) ? state.settings.workdays.map(Number) : null;
        const allOff = new Map();
        d.managers.forEach(m => m.days.forEach(day => {
            const wd = Number(day.weekday), off = (day.flags || []).includes('off_day');
            allOff.set(wd, (allOff.has(wd) ? allOff.get(wd) : true) && off);
        }));
        const work = new Set(sw || wdList.filter(w => !allOff.get(w)));
        return { byCust, W, wdList, cols, work };
    }

    const custOf = (id) => state.data.customers[String(id)] || null;
    const hasGeo = (c) => !!c && c.coord_source !== 'none' && num(c.lat) !== null && num(c.lon) !== null;
    // Остановка дня (day.stops): координата ЭТОГО визита — у клиента с двумя адресами они разные
    const stopGeo = (st) => !!st && st.coord_source !== 'none' && num(st.lat) !== null && num(st.lon) !== null;
    const hasHome = (m) => !!(m && m.home && num(m.home.lat) !== null && num(m.home.lon) !== null);
    const isOff = (day) => (day.flags || []).includes('off_day') || !state.idx.work.has(Number(day.weekday));
    function dayTitle(day) {
        return (WD_FULL[day.weekday] || ('օր ' + day.weekday)) + (state.idx.W > 1 ? ', ' + ord(day.week || 1) + ' շաբաթ' : '');
    }
    const dayShort = (day) => (WD_SHORT[day.weekday] || '?') + (state.idx.W > 1 ? ' (' + ord(day.week || 1) + ' շաբաթ)' : '');
    const mgrName = (m) => (m && m.name) ? String(m.name) : ('Մենեջեր ' + (m ? m.agent_id : ''));

    // Ожидаемая выручка ОДНОГО визита зимой. Сервер отдаёт её сам (rev_visit_low); если поля нет —
    // недельная зимняя выручка / визитов в неделю по плану (f_i, как в плане §5).
    function visitRevenue(id) {
        const c = custOf(id);
        if (!c) return null;
        if (num(c.rev_visit_low) !== null) return num(c.rev_visit_low);
        if (num(c.rev_week_low) === null) return null;
        const f = num(c.visits_per_week) || (state.idx.byCust.get(String(id)) || []).length / state.idx.W;
        return f ? num(c.rev_week_low) / f : null;
    }

    function resolveSel() {
        const s = state.sel;
        if (!s || !state.data) return null;
        const mi = state.data.managers.findIndex(m => String(m.agent_id) === s.agent);
        if (mi < 0) return null;
        const m = state.data.managers[mi];
        const di = m._byKey.get(s.week + '-' + s.weekday);
        return di === undefined ? null : { mi, di, m, day: m.days[di] };
    }

    // ---------- Узлы DOM и подсказки «?» ----------
    // Узел DOM: строки-дети становятся текстом (никакого innerHTML с данными)
    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else if (k === 'dataset') Object.assign(el.dataset, v);
            else if (typeof v === 'boolean') el[k] = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat(Infinity).forEach(c => {
            if (c === null || c === undefined || c === false) return;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        });
        return el;
    }
    const icon = (cls) => h('i', { class: 'fas ' + cls, 'aria-hidden': 'true' });
    const mark = (text, tone) => h('b', { class: 'rt-mark' + (tone > 0 ? ' is-good' : (tone < 0 ? ' is-bad' : (tone === 'warn' ? ' is-warn' : ''))), text });
    // Кнопка «?» раскрывает пояснение рядом (toggletip): клик — показать/скрыть, Esc и клик мимо — скрыть
    let tipSeq = 0;
    function tip(text, label) {
        const id = 'rtTip' + (++tipSeq);
        return h('span', { class: 'rt-tip' },
            h('button', { type: 'button', class: 'rt-tip-btn', 'aria-expanded': 'false', 'aria-controls': id,
                'aria-label': 'Բացատրություն' + (label ? '՝ ' + label : '') }, '?'),
            h('span', { class: 'rt-tip-bubble', id, role: 'note', hidden: true }, text));
    }
    function setTip(btn, open) {
        const bubble = document.getElementById(btn.getAttribute('aria-controls'));
        if (!bubble) return;
        btn.setAttribute('aria-expanded', String(open));
        bubble.hidden = !open;
        if (open) placeTip(btn, bubble);
    }
    function placeTip(btn, bubble) {
        const r = btn.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
        bubble.style.left = '0px';
        bubble.style.top = '0px';
        const w = bubble.offsetWidth, ht = bubble.offsetHeight;
        const left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, vw - w - 8));
        let top = r.bottom + 8;
        if (top + ht > vh - 8 && r.top - ht - 8 > 8) top = r.top - ht - 8;
        bubble.style.left = Math.round(left) + 'px';
        bubble.style.top = Math.round(top) + 'px';
    }
    const openTips = () => [...document.querySelectorAll('#rtOverview .rt-tip-btn[aria-expanded="true"]')];
    function initTips() {
        $('rtOverview').addEventListener('click', (e) => {
            const b = e.target.closest('.rt-tip-btn');
            if (!b) return;
            e.preventDefault();
            const open = b.getAttribute('aria-expanded') !== 'true';
            openTips().forEach(x => { if (x !== b) setTip(x, false); });
            setTip(b, open);
        });
        document.addEventListener('click', (e) => { if (!e.target.closest('.rt-tip')) openTips().forEach(x => setTip(x, false)); });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            const list = openTips();
            if (!list.length) return;
            list.forEach(x => setTip(x, false));
            list[0].focus();
        });
        const follow = () => openTips().forEach(b => placeTip(b, document.getElementById(b.getAttribute('aria-controls'))));
        window.addEventListener('scroll', follow, { passive: true });
        window.addEventListener('resize', follow);
    }

    // ---------- Рендер ----------
    function render() {
        $('rtBody').classList.remove('d-none');
        renderFresh();
        renderMain();
        renderTodo();
        renderKpis();
        renderMapTools();
        renderLegend();
        renderHeat();
        renderHeatLegend();
        renderFleet();
        renderSeason();
        renderRisk();
        ensureMap();
        renderMap();
        renderSide();
    }

    function renderFresh() {
        const d = state.data, el = $('rtFresh');
        const t = parseTime(d.data_as_of) || parseTime(d.generated_at);
        const stale = d.warnings.some(w => w && w.code === 'erp_stale');
        el.textContent = (stale ? 'ERP-ն հասանելի չէ — տվյալները ' : 'ERP-ի տվյալները ')
            + (t ? t.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) + ' դրությամբ' : '—');
        el.className = 'rt-pill' + (stale ? ' is-warn' : (d.from_cache ? ' is-cache' : ''));
        el.title = stale
            ? 'ERP-ն հիմա հասանելի չէ — ցույց են տրված նախկին տվյալները։ «Թարմացնել» կոճակը կրկին կփորձի կարդալ ERP-ն։'
            : (d.from_cache
                ? 'Հաշվարկը վերցված է սերվերի հիշողությունից։ «Թարմացնել»՝ կրկին կարդալ ERP-ն և վերահաշվել։'
                : 'Հենց նոր կարդացվեց ERP-ից');
    }

    // Порог дня словами: «100 000 դրամ»; без настроек — «օրական նորմը»; abl — отложительный («100 000 դրամից պակաս»)
    const minDayText = (abl) => {
        const v = num((state.settings || {}).min_day_revenue);
        return v !== null ? fmt(v) + NB + (abl ? 'դրամից' : 'դրամ') : (abl ? 'օրական նորմից' : 'օրական նորմը');
    };
    // «≈ 4 450» — большое число округлено, чтобы не казалось точным до километра
    function approx(v) {
        const n = num(v);
        if (n === null) return '—';
        const a = Math.abs(n), step = a >= 1000 ? 50 : (a >= 100 ? 10 : 1);
        return fmt(Math.round(n / step) * step);
    }
    const moneyApprox = (v) => { const n = num(v); if (n === null) return '—'; const a = Math.abs(n); return a >= 1e6 ? fmt(n / 1e6, 1) + NB + 'մլն' : fmt(Math.round(n / (a >= 1e4 ? 1000 : 100)) * (a >= 1e4 ? 1000 : 100)); };
    const lowMonths = () => monthsText((state.data.season || {}).low_months);
    // «4 ամսից ավելի»: «больше N месяцев»
    const lostMonths = () => {
        const d = num((state.settings || {}).lost_min_days);
        const m = Math.max(1, Math.round((d !== null ? d : 120) / 30));
        return (m === 1 ? 'մեկ ամսից' : m + ' ամսից') + ' ավելի';
    };
    const weakTip = () => 'Հաշվում ենք ձմռան պատվերներով (' + lowMonths() + ')՝ տարվա ամենաթույլ ժամանակով։ Օրը թույլ է, եթե այն, ամենայն հավանականությամբ, '
        + 'չի հավաքի ' + minDayText() + ' (հավանականությունը՝ 50%-ից պակաս)։ Հաշվվում են հաշվարկում ներառված բոլոր մենեջերների աշխատանքային օրերը մեկ շաբաթում։';
    const kmTip = () => 'Տնից մինչև օրվա խանութները՝ ամենակարճ հերթականությամբ, և հետ՝ տուն, ըստ ERP-ի պլանի։ '
        + (state.data.distance_source === 'roads' ? 'Կմ-ները՝ ըստ քարտեզի ճանապարհների։' : 'Կմ-ները՝ ուղիղ գծով, ճանապարհների ոլորունության ճշգրտումով։');
    // Парк машин посчитан, если есть склад и хотя бы одна активная машина с тоннажем и расходом (fleet)
    const truckIssue = () => {
        const d = state.data;
        const noDepot = !d.depot || num(d.depot.lat) === null || num(d.depot.lon) === null;
        return noDepot ? { hash: 'depot', text: 'նշեք պահեստը և մեքենաները' }
            : (!d.fleet ? { hash: 'trucks', text: 'նշեք մեքենաների տոննաժը և ծախսը' } : null);
    };
    const fleetTip = () => 'Մեքենան ամրացված է վարորդին, ոչ թե մենեջերին․ օրվա ընթացքում բոլոր մենեջերների պատվերները բաշխվում են '
        + 'մեքենաների և երթերի միջև միասին՝ հաշվի առնելով մեքենայի տոննաժը և աշխատանքային օրը։ Օրվա պատվերները տանում են հաջորդ աշխատանքային օրը '
        + '(շաբաթ օրվանը՝ երկուշաբթի)։ Լիտրերը՝ երթի կմ × այն տանող մեքենայի ծախսը, տարեկան միջինը։';

    // «Главное сейчас»: 2–4 предложения человеческим языком
    function renderMain() {
        const t = state.data.totals, list = $('rtMainList'), note = $('rtMainNote');
        list.textContent = '';
        note.textContent = '';
        note.hidden = true;
        const items = [];
        const below = num(t.days_below_min), total = num(t.days_total);
        if (below !== null && total) {
            items.push(below > 0
                ? { tone: -1, nodes: [fmt(total) + ' աշխատանքային օրից ', mark(fmt(below) + '-ում', -1), ' մենեջերները ձմռանը բերում են '
                    + minDayText(true) + ' պակաս'], tip: weakTip(), label: 'թույլ օրեր' }
                : { tone: 1, nodes: ['Բոլոր ' + fmt(total) + ' աշխատանքային օրերը ձմռանը բերում են ', mark('առնվազն ' + minDayText(), 1)], tip: weakTip(), label: 'թույլ օրեր' });
        }
        const mk = num(t.manager_km_week);
        if (mk !== null) {
            const extra = [num(t.manager_liters_week) !== null ? '≈' + NB + approx(t.manager_liters_week) + NB + 'լ վառելիք' : '',
                num(t.manager_amd_week) !== null ? '≈' + NB + moneyApprox(t.manager_amd_week) + NB + 'դրամ' : ''].filter(Boolean);
            items.push({ tone: 0, nodes: ['Մենեջերներն անցնում են ', mark('≈' + NB + approx(mk) + NB + 'կմ շաբաթում', 0), extra.length ? ' (' + extra.join(', ') + ')' : ''],
                tip: kmTip(), label: 'կիլոմետրեր' });
        }
        const r = t.at_risk;
        if (r && typeof r === 'object') {
            const n = (num(r.dormant) || 0) + (num(r.lost) || 0) + (num(r.never) || 0);
            const rev = (num(r.dormant_rev_year) || 0) + (num(r.lost_rev_year) || 0);
            if (n) {
                items.push({ tone: 'warn', nodes: [mark(fmt(n) + NB + 'խանութ դադարել է գնել', 'warn'),
                    rev ? ' (նախկինում տարեկան գնում էին ≈' + NB + moneyShort(rev) + NB + 'դրամի)' : '', ' — մենեջերներն իզուր են այցելում դրանց'],
                    tip: 'Դադարել է գնել՝ կանոնավոր պատվիրում էր, բայց վաղուց չի պատվիրում։ Ցուցակը ներքևում է՝ «Ռիսկային խանութներ» բաժնում։ '
                        + 'Օպտիմալացումը կառաջարկի ' + lostMonths() + ' չգնողներին հանել երթուղուց, մնացածներին՝ այցելել ավելի հազվադեպ։ Որոշում եք դուք։',
                    label: 'ռիսկային խանութներ' });
            }
        }
        items.forEach(it => list.append(h('li', { class: it.tone === 'warn' ? 'is-warn' : (it.tone > 0 ? 'is-good' : (it.tone < 0 ? 'is-bad' : 'is-same')) },
            h('span', { class: 'ico', 'aria-hidden': 'true' },
                icon(it.tone === 'warn' ? 'fa-store-slash' : (it.tone > 0 ? 'fa-check' : (it.tone < 0 ? 'fa-triangle-exclamation' : 'fa-car')))),
            h('span', { class: 'txt' }, it.nodes, ' ', tip(it.tip, it.label)))));

        // Грузовики — одной строкой: что указать, чтобы посчитать дизель, или сколько его уходит
        const issue = truckIssue(), tl = num(t.truck_liters_week), tamd = num(t.truck_amd_week), trips = num(t.trips_per_day);
        if (issue) {
            note.append(icon('fa-truck'), h('span', {}, 'Բեռնատարների դիզելը հաշվելու համար ' + issue.text + ' — ',
                h('a', { href: '/routes/settings#' + issue.hash, text: 'կարգավորումներում' }), '։'));
        } else {
            note.append(icon('fa-truck'), h('span', {}, 'Բեռնատարներն անցնում են ≈' + NB + approx(t.truck_km_week) + NB + 'կմ շաբաթում, դա ≈' + NB
                + approx(tl) + NB + 'լիտր դիզել է' + (tamd !== null ? ' (≈' + NB + moneyApprox(tamd) + NB + 'դրամ)' : '') + '։'
                + (trips !== null ? ' Օրական՝ ≈' + NB + fmt(trips, 1) + NB + 'երթ։' : '') + ' ',
                h('a', { href: '#rtFleetSection', text: 'Առաքումն ըստ օրերի' }), ' ', tip(fleetTip(), 'բեռնատարների դիզելը')));
        }
        note.hidden = false;
    }

    // «Что ещё настроить»: не больше трёх строк, главное первым, у каждой одна кнопка; технические — под «ещё N»
    function todoOf(w) {
        const d = state.data, text = String(w.text || '');
        const inc = d.managers.filter(m => m.included !== false);
        const n = (re) => { const m = re.exec(text); return m ? m[1] : null; };
        switch (w.code) {
            case 'erp_stale': return null;   // уже в плашке у заголовка
            case 'no_depot': return { p: 1, hash: 'depot', btn: 'Նշել պահեստը', text: 'Պահեստը նշված չէ — առանց դրա հնարավոր չէ հաշվել մեքենաների երթերը և դիզելը։' };
            case 'no_fleet': return { p: 2, hash: 'trucks', btn: 'Լրացնել մեքենաները',
                text: 'Նշեք մեքենաների տոննաժը և ծախսը — այդ դեպքում ծրագիրը կհաշվի բեռնատարների դիզելը։' };
            case 'truck_incomplete': {
                // прежний текст: «У 2 машин не указан…», армянский: «2 մեքենայի տոննաժը…»
                const k = n(/^(?:У )?(\d+) (?:машин|մեքենայի)/);
                return { p: 3, hash: 'trucks', btn: 'Լրացնել մեքենաները',
                    text: (k ? k + ' մեքենայի' : 'Մեքենաների մի մասի') + ' տոննաժը կամ ծախսը նշված չէ — դրանք առաքման մեջ չեն։' };
            }
            case 'fleet_short': {
                // прежний текст: «…в 1,5 дн. доставки…», армянский: «…շաբաթվա 1,5 առաքման օրում…»
                const k = n(/(?:в |շաբաթվա )([\d,.]+) (?:дн|առաքման օր)/);
                return { p: 3, hash: 'trucks', btn: 'Ստուգել մեքենաները',
                    text: 'Բարձր սեզոնին ' + (k ? 'շաբաթվա ' + k + ' առաքման օրում' : 'որոշ օրերում')
                        + ' մեքենաները չեն հասցնում աշխատանքային օրվա ընթացքում տանել պատվերները — անհրաժեշտ է ևս մեկ մեքենա կամ արտաժամյա երթ։' };
            }
            case 'no_fuel_prices': return { p: 4, hash: 'fuel', btn: 'Նշել գները',
                text: 'Վառելիքի գները նշված չեն' + (n(/\(([^)]+)\)/) ? ' (' + n(/\(([^)]+)\)/) + ')' : '') + ' — վառելիքի ծախսերը դրամով հաշվված չեն։' };
            case 'no_home': {
                // прежний текст: «Нет дома у 3 менеджеров…», армянский: «3 մենեջերի տունը…»
                const k = inc.filter(m => !hasHome(m)).length || n(/(?:^|у )(\d+) (?:менеджер|մենեջերի)/) || 1;
                return { p: 5, hash: 'managers', btn: 'Նշել տունը',
                    text: k + ' մենեջերի տունը հայտնի չէ — օրը հաշվվում է առաջին խանութից, ոչ թե տնից։' };
            }
            case 'season_unknown': return { p: 6, hash: 'season', btn: 'Նշել սեզոնները', text: 'Վաճառքի տվյալները բավարար չեն, որ ծրագիրն ինքը գտնի ձմեռն ու ամառը — նշեք ամիսները ձեռքով։' };
            case 'season_empty': return { p: 6, hash: 'season', btn: 'Ստուգել սեզոնները', text: 'Ձմեռն ու ամառն ընտրված են պահուստային կանոնով — ստուգեք ամիսները։' };
            case 'inactive_templates': {
                // коды менеджеров — между «):» (прежний текст) или «)՝» (армянский) и «—»
                const codes = n(/\)[:՝]\s*([^—]+?)\s+—/);
                return { p: 7, hash: 'managers', btn: 'Ստուգել', info: true,
                    text: (codes ? 'Մենեջերներ՝ ' + codes : 'Մենեջերների մի մասը') + ' — 8 շաբաթ առանց պատվերների և այցերի, հաշվարկում չեն։' };
            }
            case 'off_days': {
                const k = n(/(\d+)/);
                return { p: 8, info: true, text: (k ? 'Պլանում ' + k + ' օր ընկնում է' : 'Պլանի որոշ օրեր ընկնում են') + ' ոչ աշխատանքային օրվա վրա (կիրակի)։ Օպտիմալացումը կառաջարկի տեղափոխել այդ այցերը։' };
            }
            case 'coords_missing': {
                // прежний текст: «…у 5 из 100 визитов…» (цикл 2 недели — «у 2.5 из 101.5»), армянский: «…2,5 / 101,5 այցի համար…»
                const m = /(\d+(?:[.,]\d+)?)(?: из | \/ )(\d+(?:[.,]\d+)?)/.exec(text);
                const dec = (s) => s.replace('.', ',');
                return { p: 9, info: true, text: m ? 'Կոորդինատներ չկան ' + dec(m[1]) + ' / ' + dec(m[2]) + ' այցի համար — դրանց կիլոմետրերը հաշվված չեն։' : 'Այցերի մի մասի համար կոորդինատներ չկան — դրանց կիլոմետրերը հաշվված չեն։' };
            }
            case 'roads_off': case 'roads_failed':
                return { p: 10, info: true, text: 'Կիլոմետրերը հաշվվում են ուղիղ գծով՝ ճանապարհների ոլորունության ճշգրտումով — ճանապարհային քարտեզը միացված չէ։' };
            case 'roads_unsnapped': return { p: 10, info: true, text: 'Մի քանի խանութ հեռու է քարտեզի ճանապարհներից — դրանց հասնող կիլոմետրերը հաշվվում են ուղիղ գծով։' };
            case 'templates_multiweek_unverified': return { p: 11, info: true, text: 'ERP-ում կան մի քանի շաբաթվա ցիկլով երթուղիներ — ծրագիրը դրանք մեկնաբանել է ենթադրաբար։' };
            default: {
                const link = safeLink(w.link);
                return text ? { p: 12, info: !link, link, btn: 'Կարգավորել', text } : null;
            }
        }
    }

    function renderTodo() {
        const seen = new Set(), items = [];
        state.data.warnings.filter(w => w && (w.text || w.code)).forEach(w => {
            const it = todoOf(w);
            if (!it) return;
            const key = it.key || w.code || it.text;
            if (seen.has(key)) return;
            seen.add(key);
            items.push(it);
        });
        items.sort((a, b) => (a.info ? 1 : 0) - (b.info ? 1 : 0) || a.p - b.p);
        const main = items.filter(x => !x.info).slice(0, 3), rest = items.filter(x => !main.includes(x));
        const row = (x) => h('li', { class: 'rt-todo-item' + (x.info ? ' is-info' : '') },
            h('span', { class: 'txt', text: x.text }),
            (x.hash || x.link) && x.btn ? h('a', { class: 'rt-btn rt-btn-ghost rt-btn-sm', href: x.link || '/routes/settings#' + x.hash,
                'aria-label': x.btn + ': ' + x.text }, x.btn, icon('fa-arrow-right')) : null);
        const list = $('rtTodoList'), more = $('rtTodoMoreList');
        list.textContent = '';
        more.textContent = '';
        main.forEach(x => list.append(row(x)));
        rest.forEach(x => more.append(row(x)));
        $('rtTodoMore').hidden = !rest.length;
        $('rtTodoMoreSum').textContent = main.length ? 'ևս ' + rest.length + ' — ի գիտություն' : 'Ի գիտություն՝ ' + rest.length;
        if (!main.length && rest.length) $('rtTodoMore').open = false;
        $('rtTodoTitle').lastChild.textContent = main.length ? 'Ինչ դեռ պետք է կարգավորել' : 'Ի գիտություն';
        $('rtTodo').classList.toggle('d-none', !items.length);
    }

    // Четыре крупных показателя; нет данных — короткая фраза и кнопка, без прочерков
    function kpi(o) {
        return h('div', { class: 'rt-kpi' + (o.tone ? ' is-' + o.tone : '') },
            h('div', { class: 'rt-kpi-label' }, h('span', { text: o.label }), o.tip ? tip(o.tip, o.label) : null),
            o.now !== null ? h('div', { class: 'rt-kpi-val' }, h('span', { class: 'now', text: o.now }), o.unit ? h('span', { class: 'unit', text: o.unit }) : null) : null,
            h('div', { class: 'rt-kpi-sub' }, o.sub));
    }

    function renderKpis() {
        const t = state.data.totals, s = state.settings || {}, box = $('rtKpis');
        box.textContent = '';
        const dayH = workHours(s);
        const below = num(t.days_below_min), total = num(t.days_total);
        box.append(below === null || !total
            ? kpi({ label: 'Թույլ օրեր ձմռանը', now: null, sub: 'Պլանում այցերով օրեր չկան — հաշվելու բան չկա։' })
            : kpi({ label: 'Թույլ օրեր ձմռանը', tone: below > 0 ? 'bad' : 'good', now: fmt(below), unit: '/ ' + fmt(total), tip: weakTip(),
                sub: ['օր, երբ հասույթը ' + minDayText(true) + ' պակաս է'] }));

        const mk = num(t.manager_km_week), ml = num(t.manager_liters_week), mamd = num(t.manager_amd_week);
        box.append(mk === null
            ? kpi({ label: 'Մենեջերների կմ շաբաթում', now: null, sub: 'Կոորդինատներով երթուղիներ չկան — հաշվելու բան չկա։' })
            : kpi({ label: 'Մենեջերների կմ շաբաթում', now: fmt(mk), unit: 'կմ', tip: kmTip(),
                sub: ml === null ? 'վառելիքի ծախսը հաշվված չէ'
                    : ['≈' + NB + fmt(ml) + NB + 'լ վառելիք', mamd !== null ? [' · ≈' + NB, h('b', { text: fmt(mamd) }), NB + 'դրամ']
                        : [' · ', h('a', { href: '/routes/settings#fuel', text: 'նշեք վառելիքի գները' })]] }));

        const r = t.at_risk;
        if (!r || typeof r !== 'object') {
            box.append(kpi({ label: 'Ռիսկային խանութներ', now: null, sub: 'Թե ով է դադարել գնել, հաշվված չէ։' }));
        } else {
            const dormant = num(r.dormant) || 0, lost = (num(r.lost) || 0) + (num(r.never) || 0), n = dormant + lost;
            const rev = (num(r.dormant_rev_year) || 0) + (num(r.lost_rev_year) || 0);
            box.append(!n
                ? kpi({ label: 'Ռիսկային խանութներ', tone: 'good', now: '0', sub: 'բոլոր խանութները գնում են սովորականի պես' })
                : kpi({ label: 'Ռիսկային խանութներ', tone: 'warn', now: fmt(n), unit: 'խանութ',
                    tip: 'Դադարել է գնել՝ վաղուց չի պատվիրում, թեև նախկինում կանոնավոր պատվիրում էր։ Եթե չի գնում ' + lostMonths()
                        + ' կամ վերջին տարում ոչ մի անգամ, օպտիմալացումը կառաջարկի հանել երթուղուց։',
                    sub: [fmt(dormant) + '-ը դադարել են գնել վերջերս, ' + fmt(lost) + '-ը՝ վաղուց', rev ? ' · տարեկան գնում էին ≈' + NB + moneyShort(rev) + NB + 'դրամի' : '',
                        h('br'), h('a', { class: 'rt-linkbtn', href: '#rtRiskSection', text: 'Ցույց տալ ցուցակը' })] }));
        }

        const pw = num(t.avg_plan_work_hours), ph = num(t.avg_plan_hours), fw = num(t.avg_fact_work_hours), fp = num(t.avg_fact_pause_hours);
        const over = ph !== null && dayH !== null && ph > dayH;
        box.append(pw === null && fw === null
            ? kpi({ label: 'Աշխատանքային օրը խանութներում', now: null, sub: 'Այցերի մասին տվյալներ չկան — հաշվելու բան չկա։' })
            : kpi({ label: 'Աշխատանքային օրը խանութներում', tone: over ? 'bad' : null, now: pw !== null ? fmt(pw, 1) : fmt(fw, 1),
                unit: pw !== null ? 'ժ՝ ըստ պլանի' : 'ժ՝ ըստ GPS-ի',
                tip: 'Ըստ պլանի՝ այցերը և ճանապարհը խանութների միջև, օրական միջինը։ Իրականում՝ նույնը ըստ GPS-ի, վերջին 45 օրում։ '
                    + 'Ընդմիջումները՝ 15 րոպեից երկար հատվածներ առանց շարժման և առանց այցերի։',
                sub: [pw !== null && fw !== null ? ['իրականում ըստ GPS-ի՝ ', h('b', { text: fmt(fw, 1) + NB + 'ժ' })] : (fw === null ? 'GPS տվյալներ չկան' : ''),
                    fp !== null ? ', ևս ≈' + NB + fmt(fp, 1) + NB + 'ժ ընդմիջում' : '',
                    ph !== null ? [h('br'), 'տնից ճանապարհով՝ ըստ պլանի ' + fmt(ph, 1) + NB + 'ժ' + (dayH !== null ? ' / ' + fmt(dayH, 1) : '')] : ''] }));
    }

    // Правила статуса словами — с порогами из настроек (без настроек — по умолчанию, §15)
    function riskRules() {
        const s = state.settings || {};
        const v = (k, def) => (num(s[k]) !== null ? num(s[k]) : def);
        return {
            dormant: 'դադարել է գնել — ավելի քան ' + fmt(v('dormant_min_days', 45)) + NB + 'օր չի պատվիրում, և դա պատվերների միջև սովորական ընդմիջումից '
                + fmt(v('dormant_mult', 3), 1) + ' անգամ ավելի երկար է։ Օպտիմալացումը կառաջարկի այցելել ամեն շաբաթ՝ վերադարձնելու համար',
            lost: 'վաղուց չի գնում — ավելի քան ' + fmt(v('lost_min_days', 120)) + NB + 'օր կամ վերջին տարում ոչ մի պատվեր։ Օպտիմալացումը կառաջարկի հանել երթուղուց, որոշում եք դուք',
            other: 'նոր խանութները (առաջին պատվերը վերջին ' + fmt(v('status_new_days', 60)) + NB + 'օրում է) և սեզոնայինները (գնում են ամռանը) այստեղ չեն ներառվում',
        };
    }

    // ---------- Фильтры карты ----------
    function renderMapTools() {
        const d = state.data, sel = $('rtMgrFilter');
        sel.length = 1;   // «Все менеджеры» остаётся
        d.managers.forEach(m => sel.add(new Option(mgrName(m) + (m.code ? ' · ' + m.code : ''), String(m.agent_id))));
        const box = $('rtDayChips');
        box.textContent = '';
        const mk = (label, full, key) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-chip';
            b.textContent = label;
            const sr = document.createElement('span');   // имя для скринридера содержит видимый текст
            sr.className = 'rt-sr-only';
            sr.textContent = ' (' + full + ')';
            b.appendChild(sr);
            b.dataset.day = key;
            b.title = full;
            b.addEventListener('click', () => {
                if (key === 'all') state.days.clear();
                else if (state.days.has(+key)) state.days.delete(+key);
                else state.days.add(+key);
                onFilter();
            });
            return b;
        };
        box.appendChild(mk('Բոլորը', 'շաբաթվա բոլոր օրերը', 'all'));
        state.idx.wdList.forEach(w => {
            const b = mk(WD_SHORT[w], WD_FULL[w] + (state.idx.work.has(w) ? '' : ', աշխատանքային շաբաթից դուրս'), String(w));
            if (!state.idx.work.has(w)) b.classList.add('is-off');
            box.appendChild(b);
        });
        [...state.days].forEach(w => { if (!state.idx.wdList.includes(w)) state.days.delete(w); });
        const c = d.totals.coords || {};
        $('rtCoordsNote').textContent = num(c.visits_total)
            ? 'կոորդինատներ կան ' + fmt(c.visits_with_coords) + ' / ' + fmt(c.visits_total) + ' այցի համար'
              + (num(c.revenue_share_with_coords) !== null ? ' · հասույթի ' + pct(c.revenue_share_with_coords) : '')
            : '';
        syncFilterUI();
    }

    function renderLegend() {
        const box = $('rtMapLegend');
        box.textContent = '';
        state.data.managers.forEach((m, mi) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-lg';
            b.dataset.agent = String(m.agent_id);
            const dot = document.createElement('span');
            dot.className = 'rt-dot';
            dot.style.background = mgrColor(mi);
            const n = document.createElement('span');
            n.className = 'n';
            n.textContent = mgrName(m);
            b.append(dot, n);
            b.title = 'Քարտեզում ցույց տալ միայն մենեջերի հաճախորդներին՝ ' + mgrName(m);
            b.addEventListener('click', () => {
                state.mgr = state.mgr === String(m.agent_id) ? '' : String(m.agent_id);
                onFilter();
            });
            box.appendChild(b);
        });
        box.insertAdjacentHTML('beforeend',
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" style="background:#eef1f6;color:#0c0f14" aria-hidden="true"><i class="fas fa-warehouse"></i></span>Պահեստ</span>' +
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" style="border:1.5px solid #a7b0c0;border-radius:50%" aria-hidden="true"><i class="fas fa-house"></i></span>Մենեջերի տուն</span>' +
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" aria-hidden="true"><span class="rt-dot" style="background:#a7b0c0;width:7px;height:7px"></span></span>որքան մեծ է կետը, այնքան խոշոր է խանութը</span>');
        box.insertAdjacentHTML('afterbegin', '<span class="rt-lg-cap">Կետի գույնը՝ մենեջեր (սեղմեք՝ միայն նրան թողնելու համար)</span>');
        syncFilterUI();
    }

    function syncFilterUI() {
        $('rtMgrFilter').value = state.mgr;
        $('rtDayChips').querySelectorAll('.rt-chip').forEach(b => {
            const on = b.dataset.day === 'all' ? state.days.size === 0 : state.days.has(+b.dataset.day);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        $('rtMapLegend').querySelectorAll('.rt-lg[data-agent]').forEach(b => {
            b.setAttribute('aria-pressed', b.dataset.agent === state.mgr ? 'true' : 'false');
        });
    }

    function onFilter() {
        state.sel = null;   // смена фильтра выходит из режима «маршрут дня»
        saveFilter();
        syncFilterUI();
        markSelectedCell();
        renderMap();
        renderSide();
    }

    function passFilter(v) {
        const m = state.data.managers[v.mi], day = m.days[v.di];
        return (!state.mgr || String(m.agent_id) === state.mgr)
            && (!state.days.size || state.days.has(Number(day.weekday)));
    }

    // Клиенты под текущим фильтром: с координатами и без
    function filteredCustomers() {
        const pts = [];
        let noGeo = 0, visits = 0;
        const mgrs = new Set();
        state.idx.byCust.forEach((all, id) => {
            const vis = all.filter(passFilter);
            if (!vis.length) return;
            visits += vis.length;
            vis.forEach(v => mgrs.add(v.mi));
            const c = custOf(id);
            if (hasGeo(c)) pts.push({ id, c, vis, all });
            else noGeo++;
        });
        return { pts, noGeo, visits, managers: mgrs.size };
    }

    // ---------- Карта ----------
    function ensureMap() {
        if (state.map || state.mapFailed) return;
        if (typeof window.L === 'undefined') {
            state.mapFailed = true;
            const el = $('rtMap');
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Թվերը և աղյուսակը ներքևում աշխատում են։';
            return;
        }
        const map = L.map('rtMap', { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, zoomControl: false });
        L.control.zoom({ zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' }).addTo(map);   // подсказки кнопок — по-армянски (как в настройках)
        RoutesBasemap.add(map);
        map.setView(YEREVAN, 9);
        // колесо мыши масштабирует карту только после клика по ней — страница прокручивается свободно
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.custLayer = L.layerGroup().addTo(map);
        state.routeLayer = L.layerGroup().addTo(map);
        state.baseLayer = L.layerGroup().addTo(map);
    }

    function renderMap() {
        const f = filteredCustomers();
        const s = resolveSel();
        $('rtNoCoords').innerHTML = s ? ''
            : (f.noGeo ? 'քարտեզում չկա՝ <b>' + fmt(f.noGeo) + '</b> խանութ առանց կոորդինատների'
                : (f.pts.length ? 'բոլոր հաճախորդները քարտեզում են' : ''));
        updateRouteBar(s);
        if (!state.map) return;
        state.custLayer.clearLayers();
        state.routeLayer.clearLayers();
        state.baseLayer.clearLayers();
        const bounds = [];
        if (s) {
            drawRoute(s, bounds);
            drawDepot(null);
        } else {
            drawCustomers(f.pts, bounds);
            drawHomes(bounds);
            drawDepot(bounds);
        }
        state.map.invalidateSize();
        if (bounds.length) state.map.fitBounds(L.latLngBounds(bounds).pad(0.06), { animate: !RM, maxZoom: 15 });
        else state.map.setView(YEREVAN, 9, { animate: !RM });
    }

    function drawCustomers(pts, bounds) {
        pts.forEach(p => {
            const c = p.c, ll = [num(c.lat), num(c.lon)];
            const mk = L.circleMarker(ll, {
                radius: c.size === 'large' ? 7 : (c.size === 'medium' ? 5.5 : 4.5),
                color: '#0c0f14', weight: 1.5, opacity: 0.9,
                fillColor: mgrColor(p.vis[0].mi), fillOpacity: 0.92,
            });
            mk.bindTooltip(() => '<b>' + esc(c.name || 'Հաճախորդ') + '</b>'
                + '<br><span style="color:#a7b0c0">' + esc(visitsText(p.all)) + '</span>', { direction: 'top', offset: [0, -6] });
            mk.bindPopup(() => popupHTML(c, p.all), { maxWidth: 320 });
            mk.addTo(state.custLayer);
            bounds.push(ll);
        });
    }

    // «Иванов И.՝ Երկ, Հնգ» — кто и когда ездит к клиенту
    function visitsText(visits) {
        const by = new Map();
        visits.forEach(v => {
            if (!by.has(v.mi)) by.set(v.mi, []);
            by.get(v.mi).push(dayShort(state.data.managers[v.mi].days[v.di]));
        });
        return [...by.entries()].map(([mi, ds]) => mgrName(state.data.managers[mi]) + '՝ ' + ds.join(', ')).join(' · ');
    }

    // Статус словами: «не покупает 60 дней (обычно заказывал раз в 7 дней)», «ни одного заказа за год»
    function statusNote(c) {
        if (!c) return '';
        if (c.status === 'never') return 'վերջին տարում ոչ մի պատվեր';
        if (c.status === 'new') return 'առաջին պատվերը վերջերս է — չենք փոխում';
        if (c.status === 'seasonal') return 'գնում է բարձր սեզոնին, հիմա բարձր սեզոն չէ';
        const days = num(c.silent_days), every = num(c.usual_interval_days);
        if (days === null) return '';
        return 'չի գնում ' + fmt(days) + NB + 'օր' + (every !== null ? ' (սովորաբար պատվիրում էր ' + fmt(Math.round(every)) + NB + 'օրը մեկ)' : '');
    }

    // Бейдж статуса клиента (затих, потерян, …); у обычного клиента — пусто. Текст — только наш
    function statusBadge(c) {
        const st = c && STATUS[c.status];
        return st ? '<span class="rt-badge rt-st ' + st[0] + '" title="' + esc(cap(statusNote(c))) + '">' + st[1] + '</span>' : '';
    }

    // coordSource — источник точки этого визита (маршрут дня), иначе — точки клиента на карте
    function popupHTML(c, visits, coordSource) {
        const row = (k, v) => '<div class="rt-pop-row"><span>' + esc(k) + '</span><b>' + esc(v) + '</b></div>';
        const by = new Map();
        visits.forEach(v => {
            if (!by.has(v.mi)) by.set(v.mi, []);
            by.get(v.mi).push(dayShort(state.data.managers[v.mi].days[v.di]));
        });
        let mg = '';
        by.forEach((ds, mi) => {
            mg += '<div class="rt-pop-mgr"><span class="rt-dot" style="background:' + mgrColor(mi) + '"></span>'
                + esc(mgrName(state.data.managers[mi])) + ' · ' + esc(ds.join(', ')) + '</div>';
        });
        const src = COORD[coordSource || c.coord_source] || COORD.none;
        const opw = num(c.orders_per_week_year);
        const st = STATUS[c.status];
        return '<div class="rt-pop">'
            + '<div class="rt-pop-t">' + esc(c.name || 'Հաճախորդ') + '</div>'
            + '<div class="rt-pop-s">' + esc([c.code, c.area, c.group].filter(Boolean).join(' · ')) + '</div>'
            + mg + '<hr>'
            + (st ? row('Կարգավիճակ', st[1] + ' — ' + statusNote(c)) : '')
            + (SILENT.has(c.status) && num(c.rev_week_year_hist) ? row('Գնում էր շաբաթում', moneyShort(c.rev_week_year_hist)) : '')
            + row('Չափ', SIZE[c.size] || '—')
            + row('Կետը քարտեզում', src[2])
            + row('Պատվիրում է', opw === null ? '—' : 'շաբաթը ' + fmt(opw, 1) + ' անգամ')
            + row('Միջին պատվեր', money(c.avg_order_amd) + ' · ' + fmt(c.avg_order_kg) + ' կգ')
            + row('Պատվերի հավանականությունը ձմռանը', pct(c.p_low))
            + row('Հասույթ շաբաթում', 'ձմռանը ' + moneyShort(c.rev_week_low) + ' · միջինում ' + moneyShort(c.rev_week_year))
            + '</div>';
    }

    function homeMarker(m, mi) {
        const icon = L.divIcon({
            className: 'rt-pin rt-pin-home',
            html: '<span style="color:' + mgrColor(mi) + '"><i class="fas fa-house" aria-hidden="true"></i></span>',
            iconSize: [24, 24], iconAnchor: [12, 12],
        });
        return L.marker([num(m.home.lat), num(m.home.lon)], { icon, title: 'Տուն՝ ' + mgrName(m), zIndexOffset: 500 })
            .bindTooltip(() => '<b>Տուն</b> · ' + esc(mgrName(m)) + '<br><span style="color:#a7b0c0">'
                + esc(HOME_SRC[m.home.source] || '') + '</span>', { direction: 'top', offset: [0, -10] });
    }

    function drawHomes(bounds) {
        state.data.managers.forEach((m, mi) => {
            if (state.mgr && String(m.agent_id) !== state.mgr) return;
            if (!hasHome(m)) return;
            homeMarker(m, mi).addTo(state.baseLayer);
            bounds.push([num(m.home.lat), num(m.home.lon)]);
        });
    }

    function drawDepot(bounds) {
        const dp = state.data.depot;
        if (!dp || num(dp.lat) === null || num(dp.lon) === null) return;
        const icon = L.divIcon({
            className: 'rt-pin rt-pin-depot',
            html: '<span><i class="fas fa-warehouse" aria-hidden="true"></i></span>',
            iconSize: [28, 28], iconAnchor: [14, 14],
        });
        L.marker([num(dp.lat), num(dp.lon)], { icon, title: 'Պահեստ', zIndexOffset: 1000 })
            .bindTooltip('<b>Պահեստ</b> — այստեղից են մեկնում մեքենաները', { direction: 'top', offset: [0, -12] })
            .addTo(state.baseLayer);
        if (bounds) bounds.push([num(dp.lat), num(dp.lon)]);
    }

    // Маршрут дня: дом → остановки day.stops в порядке объезда (самый короткий внутри дня) → дом.
    // Точка — ЭТОГО визита (адрес шаблона), а не клиента; визиты без координат пропускаются.
    function drawRoute(s, bounds) {
        const color = mgrColor(s.mi);
        const stops = s.day.stops.map((st, i) => ({ st, id: String(st.customer_id), n: i + 1, c: custOf(st.customer_id) || {} }))
            .filter(x => stopGeo(x.st));
        const path = stops.map(x => [num(x.st.lat), num(x.st.lon)]);
        const home = hasHome(s.m) ? [num(s.m.home.lat), num(s.m.home.lon)] : null;
        if (home) { path.unshift(home); path.push(home); }
        if (path.length >= 2) {
            L.polyline(path, { color: '#0c0f14', weight: 7, opacity: 0.6, interactive: false }).addTo(state.routeLayer);
            L.polyline(path, { color, weight: 3, opacity: 0.95, lineJoin: 'round', interactive: false }).addTo(state.routeLayer);
        }
        stops.forEach(x => {
            const icon = L.divIcon({
                className: 'rt-stop',
                html: '<span style="color:' + color + '">' + x.n + '</span>',
                iconSize: [22, 22], iconAnchor: [11, 11],
            });
            const erpNo = num(x.st.erp_rownum);
            L.marker([num(x.st.lat), num(x.st.lon)], { icon, keyboard: false, riseOnHover: true })
                .bindTooltip(() => '<b>' + x.n + '.</b> ' + esc(x.c.name || 'Հաճախորդ')
                    + (erpNo !== null ? '<br><span style="color:#a7b0c0">№ ERP-ում՝ ' + fmt(erpNo) + '</span>' : ''),
                    { direction: 'top', offset: [0, -10] })
                .bindPopup(() => popupHTML(x.c, state.idx.byCust.get(x.id) || [], x.st.coord_source), { maxWidth: 320 })
                .addTo(state.routeLayer);
            bounds.push([num(x.st.lat), num(x.st.lon)]);
        });
        if (home) {
            homeMarker(s.m, s.mi).addTo(state.routeLayer);
            bounds.push(home);
        }
    }

    function updateRouteBar(s) {
        const bar = $('rtRouteBar');
        if (!s) { bar.hidden = true; return; }
        $('rtRouteBarText').textContent = 'Օրվա երթուղին՝ ' + mgrName(s.m) + ' · ' + dayTitle(s.day);
        bar.hidden = false;
    }

    // ---------- Тепловая таблица «менеджер × день» ----------
    function renderHeat() {
        const d = state.data, ix = state.idx, table = $('rtHeat');
        const empty = !d.managers.length;
        $('rtHeatEmpty').classList.toggle('d-none', !empty);
        table.classList.toggle('d-none', empty);
        const th = (c) => '<th scope="col"' + (ix.work.has(c.weekday) ? '' : ' class="is-off"') + '>'
            + '<span aria-hidden="true">' + WD_SHORT[c.weekday] + '</span><span class="rt-sr-only">'
            + WD_FULL[c.weekday] + (ix.W > 1 ? ', ' + ord(c.week) + ' շաբաթ' : '') + '</span></th>';
        let h;
        if (ix.W > 1) {
            h = '<tr class="rt-heat-weeks"><th scope="col" rowspan="2" class="rt-heat-mgr">Մենեջեր</th>';
            for (let k = 1; k <= ix.W; k++) h += '<th scope="colgroup" colspan="' + ix.wdList.length + '">' + ord(k) + ' շաբաթ</th>';
            h += '<th scope="col" rowspan="2" class="rt-heat-week">Շաբաթում</th></tr><tr>' + ix.cols.map(th).join('') + '</tr>';
        } else {
            h = '<tr><th scope="col" class="rt-heat-mgr">Մենեջեր</th>' + ix.cols.map(th).join('')
                + '<th scope="col" class="rt-heat-week">Շաբաթվա ընթացքում</th></tr>';
        }
        table.tHead.innerHTML = h;
        table.tBodies[0].innerHTML = d.managers.map(rowHTML).join('');
        markSelectedCell();
    }

    function rowHTML(m, mi) {
        const tags = [];
        if (m.included === false) tags.push('<span class="rt-badge" title="Մենեջերը ներառված չէ ընդհանուր թվերում։ Կարելի է միացնել կարգավորումներում։">հաշվարկում չէ</span>');
        if ((m.flags || []).includes('inactive')) tags.push('<span class="rt-badge b-warn" title="8 շաբաթ պատվերներ և այցեր չկան — լռելյայն մենեջերը հաշվարկում չէ">8 շաբաթ առանց աշխատանքի</span>');
        if (!hasHome(m)) tags.push('<span class="rt-badge b-warn" title="Տունը հայտնի չէ — օրը հաշվվում է առաջին հաճախորդից մինչև վերջինը">տունն անհայտ է</span>');
        let tr = '<tr' + (m.included === false ? ' class="is-excluded"' : '') + '>'
            + '<th scope="row" class="rt-heat-mgr"><div class="rt-mgr"><span class="rt-dot" style="background:' + mgrColor(mi) + '"></span>'
            + '<div class="rt-mgr-txt"><span class="n" title="' + esc(mgrName(m)) + '">' + esc(mgrName(m)) + '</span>'
            + '<span class="c">' + esc(m.code || '') + '</span>'
            + (tags.length ? '<span class="tags">' + tags.join('') + '</span>' : '') + '</div></div></th>';
        state.idx.cols.forEach((c, ci) => {
            const di = m._byKey.get(c.week + '-' + c.weekday);
            tr += '<td>' + (di === undefined
                ? '<span class="rt-hc-none" title="Այս օրը պլանով այցեր չկան">—</span>'
                : cellHTML(m, mi, m.days[di], di, ci)) + '</td>';
        });
        const wk = m.week || {};
        tr += '<td class="rt-heat-week"><span class="v">' + moneyShort(wk.revenue_low) + '</span>'
            + '<span class="s">' + fmt(wk.visits) + ' այց · ' + fmt(wk.manager_km) + ' կմ</span></td>';
        return tr + '</tr>';
    }

    function dayFlags(day) {
        const flags = day.flags || [];
        return {
            visits: num(day.visits) || 0,
            p: num(day.p_day_ge_min),
            over: flags.includes('overtime') || (num(day.overtime_minutes) || 0) > 0,
            off: isOff(day),
        };
    }

    const statusClass = (f) => !f.visits ? 's-empty' : (f.p === null ? '' : (f.p < 0.5 ? 's-bad' : (f.p < 0.8 ? 's-mid' : 's-good')));

    function cellLabel(m, day, f) {
        const minDay = num((state.settings || {}).min_day_revenue);
        let t = mgrName(m) + ', ' + dayTitle(day) + '՝ ';
        if (!f.visits) return t + 'այցեր չկան' + (f.off ? ', օրը աշխատանքային շաբաթից դուրս է' : '') + '։';
        t += fmt(f.visits) + ' այց'
            + ', ձմռանը սպասվող հասույթը՝ ' + money(day.revenue_low_exp)
            + ', ' + (minDay !== null ? fmt(minDay) + ' դրամ' : 'նորմը') + ' հավաքելու հավանականությունը՝ ' + pct(f.p);
        if (f.over) t += ', արտաժամ՝ ' + hm(day.overtime_minutes);
        if (f.off) t += ', օրը աշխատանքային շաբաթից դուրս է';
        return t + '։';
    }

    function cellHTML(m, mi, day, di, ci) {
        const f = dayFlags(day);
        const label = esc(cellLabel(m, day, f));
        const cls = 'rt-hc ' + statusClass(f) + (f.off ? ' is-off' : '');
        const attrs = ' type="button" class="' + cls + '" data-mi="' + mi + '" data-di="' + di + '" data-ci="' + ci
            + '" tabindex="-1" aria-pressed="false" aria-label="' + label + '" title="' + label + '"';
        if (!f.visits) return '<button' + attrs + '><span class="rt-hc-sub">այցեր չկան</span></button>';
        const icons = f.over ? '<i class="fas fa-clock f-over"></i>' : '';
        return '<button' + attrs + '>'
            + '<span class="rt-hc-top"><span class="rt-hc-v">' + moneyShort(day.revenue_low_exp) + '</span>'
            + (icons ? '<span class="rt-hc-flags" aria-hidden="true">' + icons + '</span>' : '') + '</span>'
            + '<span class="rt-hc-sub">' + fmt(f.visits) + ' այց · ' + pct(f.p) + '</span></button>';
    }

    function markSelectedCell() {
        const s = resolveSel();
        const cells = [...$('rtHeat').querySelectorAll('.rt-hc')];
        let current = null;
        cells.forEach(b => {
            const on = !!s && +b.dataset.mi === s.mi && +b.dataset.di === s.di;
            b.classList.toggle('is-sel', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
            if (on) current = b;
            if (b.tabIndex === 0) current = current || b;
        });
        // «Бегающий» tabindex: в таблицу попадаем одной клавишей Tab, дальше — стрелками
        const target = current || cells[0];
        cells.forEach(b => { b.tabIndex = b === target ? 0 : -1; });
    }

    function cellFor(mi, di) {
        return $('rtHeat').querySelector('.rt-hc[data-mi="' + mi + '"][data-di="' + di + '"]');
    }

    function onHeatKey(e) {
        const b = e.target.closest('.rt-hc');
        if (!b || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const rows = [...$('rtHeat').tBodies[0].rows];
        const r = rows.indexOf(b.closest('tr')), ci = +b.dataset.ci;
        const inRow = (ri) => [...rows[ri].querySelectorAll('.rt-hc')];
        let target = null;
        if (e.key === 'ArrowRight') target = inRow(r).find(x => +x.dataset.ci > ci);
        else if (e.key === 'ArrowLeft') target = inRow(r).reverse().find(x => +x.dataset.ci < ci);
        else if (e.key === 'Home') target = inRow(r)[0];
        else if (e.key === 'End') target = inRow(r).pop();
        else {
            const step = e.key === 'ArrowDown' ? 1 : -1;
            for (let ri = r + step; ri >= 0 && ri < rows.length && !target; ri += step) {
                const cs = inRow(ri);
                if (cs.length) target = cs.reduce((best, x) => Math.abs(+x.dataset.ci - ci) < Math.abs(+best.dataset.ci - ci) ? x : best);
            }
        }
        if (target) {
            b.tabIndex = -1;
            target.tabIndex = 0;
            target.focus();
        }
    }

    function renderHeatLegend() {
        const minDay = num((state.settings || {}).min_day_revenue);
        const item = (sw, txt) => '<span class="rt-hl-item">' + sw + '<span>' + txt + '</span></span>';
        $('rtHeatLegend').innerHTML =
            '<span>Վանդակի գույնը՝ ձմռանը օրը ' + (minDay !== null ? fmt(minDay) + '&nbsp;դրամ բերելու' : 'օրական նորմը հավաքելու') + ' հավանականությունը</span>'
            + item('<span class="rt-hl-sw s-bad" aria-hidden="true"></span>', '50%-ից պակաս')
            + item('<span class="rt-hl-sw s-mid" aria-hidden="true"></span>', '50–80%')
            + item('<span class="rt-hl-sw s-good" aria-hidden="true"></span>', '80% և ավելի')
            + item('<i class="fas fa-clock f-over" aria-hidden="true"></i>', 'արտաժամ')
            + item('<span class="rt-hl-sw" style="background:transparent;border:1px dashed #7c8698" aria-hidden="true"></span>', 'օր՝ աշխատանքային շաբաթից դուրս');
    }

    // ---------- Развоз по дням (парк машин) ----------
    // День доставки заказов дня визита: пн–пт → следующий день той же недели, сб и вс → пн следующей недели
    function fleetDayOf(day) {
        const f = state.data.fleet;
        if (!f || !Array.isArray(f.days)) return null;
        const w = Number(day.week) || 1, wd = Number(day.weekday), cycle = Number(f.cycle_weeks) || 1;
        const key = wd >= 1 && wd <= 5 ? [w, wd + 1] : [(w % cycle) + 1, 1];
        return f.days.find(x => Number(x.week) === key[0] && Number(x.weekday) === key[1]) || null;
    }

    // «83 պատվեր», «43 կետ»: среднее за пробу округлено до целого
    const count = (v, word) => fmt(Math.round(num(v) || 0)) + NB + word;

    function renderFleet() {
        const f = state.data.fleet, sec = $('rtFleetSection'), box = $('rtFleetDays'), lead = $('rtFleetLead');
        box.textContent = '';
        lead.textContent = '';
        if (!f || !Array.isArray(f.days)) {
            const issue = truckIssue();
            lead.append('Առաքումը հաշվված չէ՝ ', issue ? issue.text : 'տվյալներ չկան', ' — ',
                h('a', { href: '/routes/settings#' + (issue ? issue.hash : 'trucks'), text: 'կարգավորումներում' }), '։');
            sec.hidden = false;
            return;
        }
        const trucks = (f.trucks || []).map(t => (t.name ? t.name + ' ' : '') + t.car_code + ' (' + fmt(num(t.capacity_kg) / 1000, 1) + NB + 'տ, '
            + fmt(t.fuel_l_per_100km, 1) + NB + 'լ/100 կմ)');
        lead.append('Մեքենաներ՝ ' + trucks.join(', ') + '։ Մեքենայի աշխատանքային օրը՝ ' + (f.work_start || '') + '–' + (f.work_end || '')
            + '։ Թվերը՝ տարեկան միջինը, բեռնվածությունը բարձր սեզոնին՝ ամռանը։ ', tip(fleetTip(), 'առաքում'));
        const multi = (Number(f.cycle_weeks) || 1) > 1;
        f.days.forEach(d => {
            const short = num(d.p_short_peak) !== null && num(d.p_short_peak) >= P_SHORT;
            const title = (WD_FULL[d.weekday] ? cap(WD_FULL[d.weekday]) : d.label) + (multi ? ', ' + ord(d.week) + ' շաբաթ' : '');
            const trips = num(d.trips);
            const card = h('article', { class: 'rt-fleet-day' + (short ? ' is-short' : '') },
                h('header', { class: 'rt-fleet-head' },
                    h('h3', { text: title }),
                    h('span', { class: 'rt-fleet-from', text: ((Number(d.weekday) === 1 ? WD_GEN[6] : (WD_GEN[Number(d.weekday) - 1] || '')) + ' պատվերները').trim() }),
                    h('span', { class: 'rt-fleet-sum', text: (trips === null ? '—' : fmt(trips, 1) + NB + 'երթ')
                        + ' · ' + count(d.orders, 'պատվեր') + ' · ' + fmt(d.kg, 0) + NB + 'կգ · ' + fmt(d.km, 0) + NB + 'կմ · ' + fmt(d.liters, 1) + NB + 'լ' })),
                h('ul', { class: 'rt-fleet-trucks' }, (d.trucks || []).map(t => h('li', {},
                    h('span', { class: 'n', text: (t.name ? t.name + ' ' : '') + t.car_code }),
                    h('span', { class: 's', text: fmt(t.trips, 1) + NB + 'երթ · ' + count(t.stops, 'կետ') + ' · '
                        + fmt(t.kg, 0) + NB + 'կգ · ' + fmt(t.km, 0) + NB + 'կմ · ' + fmt(t.hours, 1) + NB + 'ժ · բեռնվածություն՝ ' + fmt(t.load_pct, 0) + '%' })))),
                h('p', { class: 'rt-fleet-note' }, 'Բարձր սեզոնին՝ ' + fmt(d.kg_peak, 0) + NB + 'կգ, բեռնվածություն՝ ' + fmt(d.load_pct_peak, 0) + '%',
                    short ? [' · ', mark('մեքենաները չեն հասցնում աշխատանքային օրվա ընթացքում', -1)] : ''));
            box.append(card);
        });
        sec.hidden = false;
    }

    // ---------- Панель дня ----------
    function selectDay(mi, di) {
        const m = state.data.managers[mi], day = m && m.days[di];
        if (!day) return;
        state.sel = { agent: String(m.agent_id), week: Number(day.week) || 1, weekday: Number(day.weekday) };
        markSelectedCell();
        renderMap();
        renderSide();
        const sec = $('rtMapSection');
        const top = sec.getBoundingClientRect().top, navH = syncNavOffset();
        if (top < navH || top > window.innerHeight * 0.35) sec.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'start' });
        const title = $('rtDayTitle');
        if (title) title.focus({ preventScroll: true });
        announce('Բացված է օրը՝ ' + mgrName(m) + ', ' + dayTitle(day) + '։ Երթուղին ցույց է տրված քարտեզում։');
    }

    function closeDay(focusBack) {
        const s = resolveSel();
        state.sel = null;
        markSelectedCell();
        renderMap();
        renderSide();
        if (focusBack && s) {
            const cell = cellFor(s.mi, s.di);
            if (cell) {
                $('rtHeat').querySelectorAll('.rt-hc').forEach(b => { b.tabIndex = b === cell ? 0 : -1; });
                cell.focus();
            }
        }
        announce('Օրը փակված է, քարտեզում կրկին բոլոր հաճախորդներն են');
    }

    function renderSide() {
        const box = $('rtSide'), s = resolveSel();
        box.innerHTML = s ? dayHTML(s) : emptySideHTML();
        box.scrollTop = 0;
        if (s) {
            $('rtDayClose').addEventListener('click', () => closeDay(true));
            $('rtDayBack').addEventListener('click', () => closeDay(true));
        }
    }

    function emptySideHTML() {
        const f = filteredCustomers();
        return '<div class="rt-side-empty">'
            + '<span class="ico" aria-hidden="true"><i class="fas fa-hand-pointer"></i></span>'
            + '<h3>Ընտրեք մենեջերի օրը</h3>'
            + '<p class="mb-0">Սեղմեք ներքևի «Ըստ շաբաթվա օրերի» աղյուսակի վանդակին — այստեղ կհայտնվեն այդ օրվա խանութներն այցելության հերթականությամբ, '
            + 'իսկ քարտեզում՝ երթուղին տնից և հետ։</p>'
            + '<div class="facts">'
            + '<span>Խանութներ քարտեզում</span><b>' + fmt(f.pts.length) + '</b>'
            + '<span>Առանց կոորդինատների</span><b>' + fmt(f.noGeo) + '</b>'
            + '<span>Մենեջերներ</span><b>' + fmt(f.managers) + '</b>'
            + '<span>Այցեր ցիկլում</span><b>' + fmt(f.visits) + '</b>'
            + '</div></div>';
    }

    function mstat(k, v, sub, cls, wide) {
        return '<div class="rt-mstat' + (wide ? ' is-wide' : '') + '"><span class="k">' + k + '</span>'
            + '<span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>'
            + (sub ? '<span class="s">' + sub + '</span>' : '') + '</div>';
    }

    // «հաճախորդների մոտ 5 ժ 40 րոպե (այցեր՝ 3 ժ, ճանապարհ դրանց միջև՝ 2 ժ 40 րոպե) · տնից և տուն՝ 50 րոպե»:
    // у клиентов — как факт GPS (от первого визита до последнего), весь день — с дорогой из дома
    function planSplit(m, day) {
        const work = num(day.work_minutes), commute = num(day.commute_minutes), visit = num(day.visit_minutes);
        if (work === null) return 'ճանապարհ՝ ' + hm(day.drive_minutes) + ' · հաճախորդների մոտ՝ ' + hm(day.visit_minutes);   // прежний ответ сервера
        const home = !(day.flags || []).includes('no_home') && hasHome(m);
        return 'հաճախորդների մոտ ' + hm(work)
            + (visit !== null ? ' (այցեր՝ ' + hm(visit) + ', ճանապարհ դրանց միջև՝ ' + hm(work - visit) + ')' : '')
            + ' · ' + (home ? 'տնից և տուն՝ ' + hm(commute) : 'տունը հայտնի չէ — տնից ճանապարհը հաշվված չէ');
    }

    function dayHTML(s) {
        const { m, mi, day } = s, st = state.settings || {};
        const minDay = num(st.min_day_revenue), minTrip = num(st.min_trip_revenue);
        const f = dayFlags(day), d = state.data;
        const badge = (cls, ico, txt) => '<span class="rt-badge ' + cls + '"><i class="fas ' + ico + '" aria-hidden="true"></i>' + txt + '</span>';
        const badges = [];
        if (f.over) badges.push(badge('b-danger', 'fa-clock', 'արտաժամ՝ ' + hm(day.overtime_minutes)));
        if ((day.flags || []).includes('no_home') || !hasHome(m)) badges.push(badge('', 'fa-house', 'տունը հայտնի չէ — առաջին հաճախորդից'));
        if (f.off) badges.push(badge('', 'fa-moon', 'օրը՝ աշխատանքային շաբաթից դուրս'));
        if ((day.flags || []).includes('sunday_order')) badges.push(badge('', 'fa-calendar-day', 'կիրակի օրվա պատվեր — առաքումը՝ երկուշաբթի'));

        const pcls = f.p === null ? '' : (f.p < 0.5 ? 'danger' : (f.p < 0.8 ? 'warn' : 'ok'));
        const chanceWords = f.p === null ? '' : (f.p < 0.5 ? 'հավանաբար չի հավաքի' : (f.p < 0.8 ? 'կարող է չհավաքել' : 'հավանաբար կհավաքի'));
        let stats = mstat('Հասույթը ձմռանը', money(day.revenue_low_exp),
            (num(day.revenue_low_p10) !== null && num(day.revenue_low_p90) !== null)
                ? '10 օրից 8-ում՝ ' + moneyShort(day.revenue_low_p10) + ' – ' + moneyShort(day.revenue_low_p90) : '')
            + mstat((minDay !== null ? fmt(minDay) : 'Նորմը') + ' հավաքելու հավանականություն', pct(f.p), chanceWords, pcls)
            + mstat('Այցեր', fmt(f.visits), num(day.visits_no_coords) ? fmt(day.visits_no_coords) + ' առանց կոորդինատների' : 'բոլորը՝ կոորդինատներով')
            + mstat('Մենեջերի վազքը', fmt(day.manager_km, 1) + ' կմ',
                [num(day.manager_liters) !== null ? fmt(day.manager_liters, 1) + ' լ վառելիք' : '',
                 num(day.manager_km_rownum) !== null ? 'ERP-ի հերթականությամբ կլիներ ' + fmt(day.manager_km_rownum, 1) + NB + 'կմ' : '']
                    .filter(Boolean).join(' · '))
            + mstat('Օրվա պլան', hm(day.plan_minutes), planSplit(m, day), f.over ? 'danger' : '', true);
        const fact = m.fact || null;
        if (fact && num(fact.days) > 0) {
            const parts = [];
            const planWork = num((m.week || {}).avg_work_hours);
            const work = num(fact.work_hours), pause = num(fact.pause_hours), trackDays = num(fact.track_days);
            if (work !== null) {
                // план «у клиентов» — против работы по треку (езда и стоянки); паузы — отдельно, как в плитке
                parts.push('հաճախորդների մոտ ' + fmt(work, 1) + NB + 'ժ'
                    + (planWork !== null ? ' (ըստ պլանի՝ ' + fmt(planWork, 1) + NB + 'ժ)' : '')
                    + (pause !== null ? ', գումարած դադարներ առանց այցերի՝ ≈' + NB + fmt(pause, 1) + NB + 'ժ' : ''));
            } else if (num(fact.hours) !== null) {
                parts.push('առաջին այցից մինչև վերջինի ավարտը՝ ' + fmt(fact.hours, 1) + NB + 'ժ — հետագիծ չկա, դադարները առանձնացված չեն');
            }
            if (num(fact.visits_per_day) !== null) parts.push(fmt(fact.visits_per_day, 1) + NB + 'այց');
            if (num(fact.productive_share) !== null) parts.push('պատվերով՝ ' + pct(fact.productive_share));
            // работа и паузы — по дням с треком (он короче: 45 дней против 8 недель)
            parts.push(fmt(fact.days) + NB + 'օրում'
                + (work !== null && trackDays !== null && trackDays < num(fact.days) ? ' (ըստ հետագծի՝ ' + fmt(trackDays) + NB + 'օր)' : ''));
            stats += mstat('Սովորական օր՝ ըստ GPS-ի', (fact.day_start && fact.day_end) ? esc(fact.day_start) + '–' + esc(fact.day_end) : '—', parts.join(' · '), '', true);
        }
        // Заказы дня везёт парк вместе с заказами других менеджеров — день доставки целиком
        const fd = fleetDayOf(day);
        if (fd) {
            const trips = num(fd.trips);
            stats += mstat('Առաքում' + (day.delivery_label ? ' · ' + esc(day.delivery_label) : ''),
                (trips === null ? '—' : fmt(trips, 1) + ' երթ') + ' · ' + fmt(fd.km, 0) + ' կմ',
                'բոլոր մենեջերները միասին՝ ' + count(fd.orders, 'պատվեր') + ', ' + fmt(fd.kg, 0) + ' կգ, ' + fmt(fd.liters, 1) + ' լ դիզել · '
                    + '<a href="#rtFleetSection">Առաքումն ըստ օրերի</a>', '', true);
        } else {
            const noDepot = !d.depot || num(d.depot.lat) === null;
            stats += mstat('Առաքում', '—', noDepot ? 'հաշվված չէ՝ ' + setLink('depot', 'նշեք պահեստը')
                : 'հաշվված չէ՝ ' + setLink('trucks', 'նշեք մեքենաների տոննաժը և ծախսը'), '', true);
        }

        // Остановки — в порядке объезда; точка и её источник — этого визита (адрес шаблона)
        const stops = day.stops;
        const rows = stops.map((st, i) => {
            const id = st.customer_id, c = custOf(id), geo = stopGeo(st);
            const src = COORD[st.coord_source] || COORD.none;
            const rev = visitRevenue(id);
            const badge = '<span class="rt-badge ' + src[0] + '" title="' + esc(src[2]) + '">' + src[1] + '</span>';
            const size = (c && SIZE[c.size]) || '—';
            return '<tr' + (geo ? '' : ' class="no-geo"') + '>'
                + '<td class="no">' + (i + 1) + '</td>'
                + '<td class="no">' + (num(st.erp_rownum) === null ? '—' : fmt(st.erp_rownum)) + '</td>'
                + '<td class="l nm"><span class="n">' + esc(c ? (c.name || 'Հաճախորդ') : 'Հաճախորդ ' + id) + '</span>'
                + '<span class="c">' + esc(c ? (c.code || '') : String(id)) + '</span>'
                + statusBadge(c)                                                                     // §15: затих, потерян, …
                + '<span class="rt-dl-meta">' + badge + '<span>' + size + '</span></span></td>'   // телефон: вместо двух колонок
                + '<td class="l rt-dl-wide">' + badge + '</td>'
                + '<td class="l rt-dl-wide">' + size + '</td>'
                + '<td>' + pct(c && c.p_low) + '</td>'
                + '<td>' + (rev === null ? '—' : fmt(Math.round(rev))) + '</td>'
                + '<td>' + fmt(c && c.avg_order_kg) + '</td></tr>';
        }).join('');
        const noGeo = stops.filter(st => !stopGeo(st)).length;
        const silent = stops.some(st => SILENT.has((custOf(st.customer_id) || {}).status));
        const list = stops.length
            ? '<div class="rt-daylist-wrap"><table class="rt-daylist"><caption class="rt-sr-only">Օրվա հաճախորդներն այցելության հերթականությամբ</caption><thead><tr>'
              + '<th scope="col" class="wrap" title="Այցելության հերթականությունը՝ օրվա ներսում ամենակարճը">Այցի №</th>'
              + '<th scope="col" class="wrap" title="Հերթականությունը ERP-ի երթուղում">№ ERP-ում</th>'
              + '<th scope="col" class="l">Հաճախորդ</th><th scope="col" class="l rt-dl-wide">Կետ</th><th scope="col" class="l rt-dl-wide">Չափ</th>'
              + '<th scope="col" class="wrap" title="Հավանականությունը, որ հաճախորդը ձմռանը այցի ժամանակ կպատվիրի">Պատվերի %</th>'
              + '<th scope="col" title="Այս այցի սպասվող հասույթը ձմռանը, դրամ">Հասույթ</th>'
              + '<th scope="col" title="Պատվերի միջին քաշը վերջին տարում">Կգ</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
              + '<p class="rt-day-note mt-2 mb-0">' + ORDER_NOTE + ' «Պատվերի %»՝ ձմռանը այցի ժամանակ պատվերի հավանականությունը, «Հասույթ»՝ այդ հավանականությունը × ձմռան միջին պատվերը։ Կգ՝ միջին պատվերը վերջին տարում։'
              + (silent ? ' «դադարել է գնել» և «վաղուց չի գնում» կարգավիճակով հաճախորդների հավանականությունը և հասույթը 0 է։' : '') + '</p>'
            : '<p class="rt-empty px-0">Այս օրը պլանով այցեր չկան։</p>';

        return '<div class="rt-day" id="rtDay">'
            + '<div class="rt-day-head"><span class="rt-dot" style="background:' + mgrColor(mi) + '"></span>'
            + '<div class="rt-day-titles"><h3 class="rt-day-title" id="rtDayTitle" tabindex="-1">'
            + esc(mgrName(m)) + ' · ' + esc(dayTitle(day)) + '</h3>'
            + '<div class="rt-day-sub">' + (day.delivery_label ? 'առաքումը՝ ' + esc(day.delivery_label) + ' · ' : '')
            + (hasHome(m) ? 'տնից և հետ' : 'տունը հայտնի չէ') + '</div></div>'
            + '<button type="button" class="rt-iconbtn" id="rtDayClose" aria-label="Փակել օրը և վերադառնալ աղյուսակին">'
            + '<i class="fas fa-xmark" aria-hidden="true"></i></button></div>'
            + (badges.length ? '<div class="rt-day-flags">' + badges.join('') + '</div>' : '')
            + '<div class="rt-day-stats">' + stats + '</div>'
            + (noGeo ? '<p class="rt-day-note"><i class="fas fa-location-crosshairs" aria-hidden="true"></i>'
                + fmt(noGeo) + ' խանութ առանց կոորդինատների — քարտեզում դրանք չկան, գիծը շրջանցում է դրանք։</p>' : '')
            + list
            + '<div class="rt-day-actions"><button type="button" class="rt-btn rt-btn-ghost" id="rtDayBack">'
            + '<i class="fas fa-arrow-left" aria-hidden="true"></i>Վերադառնալ աղյուսակին</button></div>'
            + '</div>';
    }

    // ---------- Легенда сезонов ----------
    // Действующие нормы дорог и визитов и откуда они: число из настроек, калибровка по GPS или по умолчанию
    function normsItems(norms) {
        const src = (n) => NORM_SRC[n.source] || 'աղբյուրը հայտնի չէ';
        const item = (key, label, d, unit) => {
            const n = norms[key];
            if (!n || num(n.value) === null) return null;
            return label + ' ' + fmt(n.value, d) + unit + ' — ' + src(n);
        };
        const sizes = [['small', 'փոքր'], ['medium', 'միջին'], ['large', 'խոշոր']]
            .map(([k, label]) => ({ label, n: norms['visit_min_' + k] }));
        let visit = null;
        if (sizes.every(x => x.n && num(x.n.value) !== null)) {
            const same = sizes.every(x => x.n.source === sizes[0].n.source);
            visit = 'այցը հաճախորդի մոտ՝ ' + sizes.map(x => x.label + ' ' + fmt(x.n.value, 1) + (same ? '' : ' (' + src(x.n) + ')')).join(' · ')
                + NB + 'րոպե' + (same ? ' — ' + src(sizes[0].n) : '');
        }
        return [
            item('detour_factor', 'ճանապարհների ոլորունություն', 2, ''),
            item('speed_city_kmh', 'արագությունը քաղաքում', 1, NB + 'կմ/ժ'),
            item('speed_region_kmh', 'արագությունը մարզում', 1, NB + 'կմ/ժ'),
            visit,
        ].filter(Boolean);
    }

    function renderSeason() {
        const se = state.data.season || {}, st = state.settings || {};
        const minDay = num(st.min_day_revenue), minTrip = num(st.min_trip_revenue);
        const low = (Array.isArray(se.low_months) ? se.low_months : []).map(Number);
        const peak = (Array.isArray(se.peak_months) ? se.peak_months : []).map(Number);
        const card = (cls, ico, title, when, items) => '<div class="rt-season-card ' + cls + '">'
            + '<h3><i class="fas ' + ico + '" aria-hidden="true"></i>' + title + '</h3>'
            + '<div class="when">' + when + '</div><ul>' + items.map(x => '<li>' + x + '</li>').join('') + '</ul></div>';
        $('rtSeason').innerHTML =
            card('is-low', 'fa-snowflake', 'Ձմեռ՝ ցածր սեզոն', monthsText(low), [
                'օրվա հասույթը և ' + (minDay !== null ? fmt(minDay) + ' դրամ' : 'օրական նորմը') + ' հավաքելու հավանականությունը',
                'թույլ օրերը աղյուսակում',
            ])
            + card('is-peak', 'fa-sun', 'Ամառ՝ բարձր սեզոն', monthsText(peak), [
                'մեքենաների բեռնվածությունը բարձր սեզոնին',
                'արդյոք մեքենաները հասցնում են աշխատանքային օրվա ընթացքում տանել պատվերները',
            ])
            + card('is-year', 'fa-calendar', 'Ամբողջ տարին', 'միջինը՝ 12 ամսում', [
                'բեռնատարների դիզելը և վազքը',
                'երթերի քանակը և այն երթերը, որտեղ մեքենան տանում է ' + (minTrip !== null ? fmt(minTrip) + ' դրամից' : 'երթի նվազագույն արժեքից') + ' պակաս',
                'հաճախորդի հասույթը «միջինում»',
            ])
            + card('is-plan', 'fa-route', 'Օրվա երթուղին', 'սեզոնից կախված չէ', [
                'մենեջերների վազքը և վառելիքը',
                'աշխատանքային օրվա ժամերը',
                'օրվա խանութները՝ ERP-ի երթուղիներից, այցելության հերթականությունը՝ օրվա ներսում ամենակարճը',
            ].concat(normsItems(state.data.norms)));
        $('rtSeasonSrc').textContent = se.source === 'manual'
            ? 'Սեզոնների ամիսները նշված են ձեռքով՝ կարգավորումներում։'
            : (se.source === 'mixed'
                ? 'Սեզոնների ամիսների մի մասը նշված է ձեռքով՝ կարգավորումներում, մնացածը գտնվել են ավտոմատ՝ ըստ վաճառքի։'
                : 'Սեզոնների ամիսները գտնվել են ավտոմատ․ ձմեռը այն ամիսներն են, որոնց վաճառքը նկատելիորեն ցածր է միջին ամսից, ամառը՝ նկատելիորեն բարձր (ըստ վերջին երեք տարիների)։ Փոխել կարելի է կարգավորումներում։');
        const idx = (se.index && typeof se.index === 'object') ? se.index : {};
        const vals = [];
        for (let m = 1; m <= 12; m++) vals.push(num(idx[m] ?? idx[String(m)]));
        const box = $('rtMonths');
        if (!vals.some(v => v !== null)) { box.innerHTML = ''; return; }
        const top = Math.max(1.5, ...vals.filter(v => v !== null)) * 1.05;
        box.innerHTML = vals.map((v, i) => {
            const m = i + 1, cls = low.includes(m) ? ' is-low' : (peak.includes(m) ? ' is-peak' : '');
            return '<div class="rt-month' + cls + '"><div class="rt-month-track" style="--one:' + (100 / top).toFixed(1) + '%">'
                + '<div class="rt-month-bar" style="height:' + (v === null ? 0 : Math.max(2, v / top * 100)).toFixed(1) + '%"></div></div>'
                + '<span class="m">' + MONTHS[i] + '</span><span class="i">' + (v === null ? '—' : fmt(v, 2)) + '</span></div>';
        }).join('');
    }

    // ---------- Клиенты под риском (§15) ----------
    // Сортировка: по выручке за год (сколько брал) или по дням без заказов; «без заказов за год» — как
    // самые долгие. Строки — только через esc(): имена и коды — из ERP
    function riskSorted() {
        const rows = state.data.at_risk_customers.slice();
        const days = (x) => (num(x.silent_days) === null ? Infinity : num(x.silent_days));
        const rev = (x) => num(x.rev_year_hist) || 0;
        rows.sort(state.riskSort === 'days'
            ? (a, b) => days(b) - days(a) || rev(b) - rev(a) || num(a.customer_id) - num(b.customer_id)
            : (a, b) => rev(b) - rev(a) || days(b) - days(a) || num(a.customer_id) - num(b.customer_id));
        return rows;
    }

    function riskRowHTML(x) {
        const st = STATUS[x.status] || ['', String(x.status || '—')];
        const mgrs = (Array.isArray(x.managers) ? x.managers : []).filter(m => m && typeof m === 'object');
        const every = num(x.usual_interval_days);
        const td = (label, cls, html) => '<td data-label="' + label + '"' + (cls ? ' class="' + cls + '"' : '') + '>' + html + '</td>';
        return '<tr>'
            + '<td class="rt-cell-name"><span class="n">' + esc(x.name || 'Հաճախորդ ' + x.customer_id) + '</span>'
            + '<span class="c">' + esc(x.code || '') + '</span></td>'
            + td('Մենեջեր', 'rt-risk-mgr', mgrs.length ? mgrs.map(m => '<span>' + esc(m.name || m.code || '')
                + (m.name && m.code ? ' <small>' + esc(m.code) + '</small>' : '') + '</span>').join('') : '—')
            + td('Ինչ է պատահել', '', '<span class="rt-badge rt-st ' + st[0] + '">' + esc(st[1]) + '</span>')
            + td('Չի գնում, օր', 'w-half num', num(x.silent_days) === null ? '—' : fmt(x.silent_days))
            + td('Սովորաբար պատվիրում էր', 'w-half num', every === null ? '—' : fmt(Math.round(every)) + NB + 'օրը մեկ')
            + td('Պատվեր՝ վերջին տարում', 'w-half num', fmt(x.orders_year))
            + td('Գնել է վերջին տարում', 'w-half num', money(x.rev_year_hist))
            + td('Վերջին պատվեր', 'w-half', dateRu(x.last_order_date))
            + '</tr>';
    }

    function renderRisk() {
        const rows = riskSorted(), n = rows.length, rules = riskRules();
        const r = state.data.totals.at_risk || {};
        const lead = $('rtRiskLead');
        lead.textContent = '';
        lead.append('Նախկինում կանոնավոր պատվիրում էին, իսկ հիմա չեն պատվիրում։ Օպտիմալացումը կառաջարկի վերջերս դադարածներին '
            + 'այցելել ամեն շաբաթ, իսկ վաղուց չգնողներին՝ հանել երթուղուց։ Որոշում եք դուք։ ',
            tip(cap(rules.dormant) + '։ ' + cap(rules.lost) + '։ ' + cap(rules.other) + '։ Այստեղ՝ հաշվարկում ներառված մենեջերների խանութները։', 'ինչպես են ընտրվում խանութները'));
        $('rtRiskSort').querySelectorAll('.rt-chip').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sort === state.riskSort)));
        const shown = state.riskAll ? rows : rows.slice(0, RISK_PAGE);
        $('rtRiskBody').innerHTML = shown.map(riskRowHTML).join('');
        $('rtRiskEmpty').classList.toggle('d-none', n > 0);
        document.querySelector('.rt-risk-scroll').classList.toggle('d-none', !n);
        $('rtRiskSort').hidden = n < 2;
        const rev = (num(r.dormant_rev_year) || 0) + (num(r.lost_rev_year) || 0);
        $('rtRiskCount').textContent = n ? fmt(n) + NB + 'խանութ'
            + (rev ? ' · տարեկան գնում էին ' + moneyShort(rev) + NB + 'դրամի' : '') : '';
        const more = $('rtRiskMore');
        more.hidden = n <= RISK_PAGE;
        more.textContent = state.riskAll ? 'Ծալել ցուցակը' : 'Ցույց տալ բոլորը (' + fmt(n) + ')';
        more.setAttribute('aria-expanded', String(state.riskAll));
    }

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        loadFilter();
        syncNavOffset();
        initTips();
        window.addEventListener('resize', syncNavOffset);
        $('rtRefreshBtn').addEventListener('click', () => load(true));
        $('rtRetryBtn').addEventListener('click', () => load(state.lastRefresh));
        $('rtMgrFilter').addEventListener('change', (e) => { state.mgr = e.target.value; onFilter(); });
        $('rtRouteClear').addEventListener('click', () => closeDay(false));
        const heat = $('rtHeat');
        heat.addEventListener('click', (e) => {
            const b = e.target.closest('.rt-hc');
            if (b) selectDay(+b.dataset.mi, +b.dataset.di);
        });
        heat.addEventListener('keydown', onHeatKey);
        $('rtSide').addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && state.sel) { e.preventDefault(); closeDay(true); }
        });
        $('rtRiskSort').addEventListener('click', (e) => {
            const b = e.target.closest('.rt-chip');
            if (!b || !state.data || b.dataset.sort === state.riskSort) return;
            state.riskSort = b.dataset.sort === 'days' ? 'days' : 'rev';
            renderRisk();
            announce(state.riskSort === 'days' ? 'Ցուցակը դասավորված է ըստ առանց պատվերի օրերի' : 'Ցուցակը դասավորված է ըստ տարեկան հասույթի');
        });
        $('rtRiskMore').addEventListener('click', () => {
            if (!state.data) return;
            state.riskAll = !state.riskAll;
            renderRisk();
        });
        load(false);
    });
})();

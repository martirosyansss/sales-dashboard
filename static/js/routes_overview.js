/* «Маршруты · как сейчас» /routes — оценка текущего плана из ERP (этап 1).
   Данные: GET /api/routes/overview (контракт — docs/plans/stage-1-plan.md §10.1).
   Пороги (100 000 / 150 000) и длина рабочего дня — из GET /api/routes/settings (§10.2);
   если настройки не пришли, тексты обходятся без чисел порогов.
   Безопасность: всё, что пришло из ERP (имена, коды, группы, территории, машины), выводится
   только через esc() или textContent — в том числе в подсказках и попапах Leaflet. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    const WD_SHORT = { 1: 'Пн', 2: 'Вт', 3: 'Ср', 4: 'Чт', 5: 'Пт', 6: 'Сб', 7: 'Вс' };
    const WD_FULL = { 1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота', 7: 'воскресенье' };
    const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    const MONTHS_FULL = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
    // Цвета менеджеров: фиксированный порядок 12 слотов. Соседние слоты различимы и при
    // дальтонизме (CVD ΔE ≥ 17, обычное зрение ΔE ≥ 17), контраст к тёмному фону ≥ 3:1.
    // Цвет закреплён за позицией менеджера в ответе API — фильтр не перекрашивает точки.
    const MGR_COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3',
                        '#0d9298', '#b8b90c', '#ae55c1', '#37981b', '#fe80c0', '#d64651'];
    const MGR_OTHER = '#8b93a7';   // 13-й и дальше — нейтральный серый, цвета не повторяем
    const SIZE = { small: 'мелкий', medium: 'средний', large: 'крупный' };
    const COORD = {
        erp: ['b-erp', 'ERP', 'координата из адреса в ERP'],
        gps: ['b-gps', 'GPS', 'по GPS визитов менеджера'],
        manual: ['b-manual', 'вручную', 'поставлена вручную'],
        none: ['b-none', 'нет', 'координаты нет — на карте не показан'],
    };
    const HOME_SRC = { manual: 'указан вручную', gps_auto: 'найден по GPS автоматически', none: 'не известен' };
    const NORM_SRC = { manual: 'задано вручную', gps: 'по GPS-трекам', default: 'по умолчанию' };
    const ORDER_NOTE = 'Порядок объезда — самый короткий внутри дня (так менеджеры фактически ездят по GPS). № в ERP — порядок в шаблоне.';
    const P_POOR = 0.5;       // «бедный рейс»: P(0 < выручка рейса < порога) ≥ 0.5
    const P_OVERFLOW = 0.2;   // «не влезет летом»: P(груз пикового дня > тоннажа) ≥ 0.2
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
    const money = (v) => num(v) === null ? '—' : fmt(Math.round(num(v))) + NB + 'драм';
    function moneyShort(v) {
        const n = num(v);
        if (n === null) return '—';
        const a = Math.abs(n);
        if (a >= 1e6) return fmt(n / 1e6, 1) + ' млн';
        if (a >= 1e3) return fmt(Math.round(n / 1e3)) + ' тыс';
        return fmt(Math.round(n));
    }
    const pct = (p) => num(p) === null ? '—' : Math.round(num(p) * 100) + '%';
    function hm(minutes) {
        const n = num(minutes);
        if (n === null) return '—';
        const m = Math.max(0, Math.round(n)), h = Math.floor(m / 60), r = m % 60;
        return h ? (r ? h + ' ч ' + r + ' мин' : h + ' ч') : r + ' мин';
    }
    function plural(n, one, few, many) {
        const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
        if (a > 10 && a < 20) return many;
        if (b > 1 && b < 5) return few;
        if (b === 1) return one;
        return many;
    }
    // «из 21 дня», «из 60 дней» — родительный падеж после «из N»
    const genDays = (n) => (n % 10 === 1 && n % 100 !== 11) ? 'дня' : 'дней';
    // Слово к числу, показанному с 1 знаком: «1 раз», «2 раза», «5 раз», но «0,8 раза», «1,1 рейса»
    function unitWord(v, one, few, many) {
        const n = num(v);
        if (n === null) return many;
        const r = Math.round(n * 10) / 10;
        return Number.isInteger(r) ? plural(r, one, few, many) : few;
    }
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
        if (!ms.length) return 'не определены';
        if (ms.length === 12) return 'весь год';
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
    async function fetchJSON(url) {
        let resp;
        try {
            resp = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store' });
        } catch (e) {
            throw Object.assign(new Error('Нет связи с сервером. Проверьте сеть и нажмите «Повторить».'), { network: true });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!data || typeof data !== 'object') {
            throw Object.assign(new Error('Сервер ответил непонятно (код ' + resp.status + '). Попробуйте ещё раз.'), { status: resp.status });
        }
        if (!resp.ok || data.success !== true) {
            throw Object.assign(new Error(data.error || ('Ошибка сервера (код ' + resp.status + ').')), { status: resp.status });
        }
        return data;
    }

    function setBusy(on) {
        const btn = $('rtRefreshBtn');
        btn.disabled = on;
        btn.setAttribute('aria-busy', on ? 'true' : 'false');
        btn.querySelector('i').className = on ? 'rt-spin-inline' : 'fas fa-rotate';
        btn.querySelector('span').textContent = on ? 'Считаю…' : 'Обновить';
    }

    async function load(refresh) {
        if (state.loading) return;
        state.loading = true;
        state.lastRefresh = !!refresh;
        const first = !state.data;
        setBusy(true);
        $('rtError').classList.add('d-none');
        if (first) $('rtLoading').classList.remove('d-none');
        else announce('Читаю данные из ERP заново…');
        try {
            const [ov, st] = await Promise.allSettled([
                fetchJSON('/api/routes/overview' + (refresh ? '?refresh=1' : '')),
                fetchJSON('/api/routes/settings'),
            ]);
            if (ov.status === 'rejected') throw ov.reason;
            state.settings = (st.status === 'fulfilled' && st.value.settings) ? st.value.settings : null;
            setData(ov.value);
            const stale = state.data.warnings.some(w => w && w.code === 'erp_stale');
            if (!first) announce(stale ? 'ERP недоступна — на экране прежние данные' : 'Данные обновлены');
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
        $('rtErrorText').textContent = (keepOld ? 'Не удалось обновить — на экране прежние данные. ' : 'Не удалось загрузить маршруты. ')
            + ((e && e.message) || 'Неизвестная ошибка.');
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
        return (WD_FULL[day.weekday] || ('день ' + day.weekday)) + (state.idx.W > 1 ? ', неделя ' + (day.week || 1) : '');
    }
    const dayShort = (day) => (WD_SHORT[day.weekday] || '?') + (state.idx.W > 1 ? ' нед.' + (day.week || 1) : '');
    const mgrName = (m) => (m && m.name) ? String(m.name) : ('Менеджер ' + (m ? m.agent_id : ''));

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

    // ---------- Рендер ----------
    function render() {
        $('rtBody').classList.remove('d-none');
        renderFresh();
        renderChecklist();
        renderTiles();
        renderMapTools();
        renderLegend();
        renderHeat();
        renderHeatLegend();
        renderSeason();
        ensureMap();
        renderMap();
        renderSide();
    }

    function renderFresh() {
        const d = state.data, el = $('rtFresh');
        const t = parseTime(d.data_as_of) || parseTime(d.generated_at);
        const stale = d.warnings.some(w => w && w.code === 'erp_stale');
        el.textContent = 'ДАННЫЕ НА ' + (t ? t.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—')
            + (stale ? ' · ERP НЕДОСТУПНА' : (d.from_cache ? ' · ИЗ ПАМЯТИ' : ''));
        el.classList.toggle('is-cache', !!d.from_cache || stale);
        el.title = stale
            ? 'ERP сейчас недоступна — показаны прежние данные. «Обновить» попробует прочитать ERP снова.'
            : (d.from_cache
                ? 'Расчёт взят из памяти сервера. «Обновить» — прочитать ERP заново и пересчитать.'
                : 'Только что прочитано из ERP');
        el.classList.remove('d-none');
    }

    function renderChecklist() {
        const list = $('rtCheckList');
        list.textContent = '';
        const warns = (Array.isArray(state.data.warnings) ? state.data.warnings : []).filter(w => w && w.text);
        // сначала то, что настраивается (есть ссылка), потом справочные пометки
        const items = warns.map(w => ({ text: String(w.text), link: safeLink(w.link) }))
            .sort((a, b) => (b.link ? 1 : 0) - (a.link ? 1 : 0));
        const anyLink = items.some(w => w.link);
        items.forEach(w => {
            const li = document.createElement('li');
            li.className = 'rt-check-item' + (w.link ? '' : ' is-info');
            const box = document.createElement('span');
            box.className = 'rt-check-box';
            box.setAttribute('aria-hidden', 'true');
            const txt = document.createElement('span');
            txt.className = 'txt';
            txt.textContent = w.text;
            li.append(box, txt);
            if (w.link) {
                const a = document.createElement('a');
                a.className = 'rt-btn rt-btn-ghost rt-btn-sm rt-check-go';
                a.href = w.link;
                a.setAttribute('aria-label', 'Настроить: ' + w.text);
                a.innerHTML = 'Настроить <i class="fas fa-arrow-right" aria-hidden="true"></i>';
                li.appendChild(a);
            }
            list.appendChild(li);
        });
        $('rtCheckTitle').textContent = anyLink ? 'Что осталось настроить' : 'Обратите внимание';
        document.querySelector('#rtChecklist .rt-check-note').textContent = anyLink
            ? 'без этого часть цифр не посчитана' : 'как читать цифры ниже';
        $('rtChecklist').classList.toggle('d-none', !items.length);
    }

    // Плитка: val/sub — только числа и статичный текст (без строк из ERP)
    function tile(id, o) {
        const el = $(id);
        el.className = 'rt-tile' + (o.cls ? ' ' + o.cls : '');
        const v = el.querySelector('[data-v]');
        v.innerHTML = o.val;
        v.classList.toggle('is-empty', !!o.empty);
        el.querySelector('[data-s]').innerHTML = o.sub || '';
        if (o.title) el.title = o.title; else el.removeAttribute('title');
    }

    function renderTiles() {
        const d = state.data, t = d.totals, s = state.settings || {};
        const minDay = num(s.min_day_revenue), minTrip = num(s.min_trip_revenue), dayH = workHours(s);
        const noDepot = !d.depot || num(d.depot.lat) === null || num(d.depot.lon) === null;
        const noTrucks = !d.managers.some(m => m.included !== false && m.truck);
        const truckHint = noDepot ? setLink('depot', 'укажите склад')
            : (noTrucks ? setLink('trucks', 'назначьте машины менеджерам') : null);

        // 1. Слабые дни зимой: N из M дают меньше 100 000 драм
        const below = num(t.days_below_min), total = num(t.days_total);
        if (below === null || !total) {
            tile('kpiWeak', { val: '—', empty: true, sub: 'в плане нет дней с визитами' });
        } else {
            tile('kpiWeak', {
                cls: below > 0 ? 't-danger' : 't-ok',
                val: fmt(below) + ' <small>из ' + fmt(total) + '</small>',
                sub: genDays(total) + ' ' + (below % 10 === 1 && below % 100 !== 11 ? 'даёт' : 'дают') + ' меньше '
                    + (minDay !== null ? fmt(minDay) + NB + 'драм' : 'дневной нормы'),
                title: num(t.avg_p_day_ge_min) !== null
                    ? 'В среднем день набирает норму с шансом ' + pct(t.avg_p_day_ge_min) + '. Слабый день — шанс меньше 50%. Считаем по зимним заказам.'
                    : 'Слабый день — шанс набрать норму меньше 50%. Считаем по зимним заказам.',
            });
        }

        // 2. Бедные рейсы: ≈N в неделю меньше 150 000
        const poor = num(t.trips_poor_week);
        if (truckHint) {
            tile('kpiPoor', { val: '—', empty: true, sub: 'не посчитаны: ' + truckHint });
        } else if (poor === null) {
            tile('kpiPoor', { val: '—', empty: true, sub: 'нет данных о рейсах' });
        } else {
            tile('kpiPoor', {
                cls: poor >= 1 ? 't-warn' : 't-ok',
                val: '≈' + fmt(poor, poor < 10 ? 1 : 0),
                sub: 'рейсов в неделю везут меньше ' + (minTrip !== null ? fmt(minTrip) + NB + 'драм' : 'нормы рейса'),
                title: 'Бедный рейс — машина везёт заказов меньше нормы рейса. Считаем по зимним заказам.',
            });
        }

        // 3. Дизель грузовиков: N л/нед (≈ драм)
        const tl = num(t.truck_liters_week), tamd = num(t.truck_amd_week);
        if (truckHint) {
            tile('kpiDiesel', { cls: 't-acc', val: '—', empty: true, sub: 'не посчитан: ' + truckHint });
        } else if (tl === null) {
            tile('kpiDiesel', { cls: 't-acc', val: '—', empty: true, sub: setLink('trucks', 'укажите расход машин') });
        } else {
            tile('kpiDiesel', {
                cls: 't-acc',
                val: fmt(tl) + ' <small>л/нед</small>',
                sub: fmt(t.truck_km_week) + NB + 'км · ' + (tamd !== null ? '≈' + NB + '<b>' + fmt(tamd) + '</b>' + NB + 'драм' : setLink('norms', 'укажите цену дизеля')),
                title: 'Считаем по среднему году: сколько заказов в среднем везут машины.',
            });
        }

        // 4. Пробег менеджеров: N км/нед
        const mk = num(t.manager_km_week), ml = num(t.manager_liters_week), mamd = num(t.manager_amd_week);
        if (mk === null) {
            tile('kpiMgrKm', { val: '—', empty: true, sub: 'нет маршрутов с координатами' });
        } else {
            tile('kpiMgrKm', {
                val: fmt(mk) + ' <small>км/нед</small>',
                sub: (ml !== null ? fmt(ml) + NB + 'л топлива' : 'расход не посчитан')
                    + (mamd !== null ? ' · ≈' + NB + '<b>' + fmt(mamd) + '</b>' + NB + 'драм' : (ml !== null ? ' · ' + setLink('norms', 'укажите цены топлива') : '')),
                title: 'По плану: дом → клиенты дня в самом коротком порядке объезда → дом, по прямой с поправкой на извилистость дорог.',
            });
        }

        // 5. Загрузка машин летом: N%
        const load = num(t.avg_load_pct_peak);
        if (truckHint) {
            tile('kpiLoad', { val: '—', empty: true, sub: 'не посчитана: ' + truckHint });
        } else if (load === null) {
            tile('kpiLoad', { val: '—', empty: true, sub: setLink('trucks', 'укажите тоннаж машин') });
        } else {
            tile('kpiLoad', {
                cls: load > 100 ? 't-danger' : (load >= 85 ? 't-warn' : ''),
                val: fmt(load) + '<small>%</small>',
                sub: 'тоннажа в загруженный летний день' + (load > 100 ? ' — нужен второй рейс' : ''),
                title: 'Летний пик: так заполнена машина в тяжёлый день (9 из 10 летних дней — легче).',
            });
        }

        // 6. Рабочий день: у клиентов: план X ч · факт по GPS Y ч; плюс паузы без визитов ≈ Z ч в день;
        // с дорогой из дома — W ч из 9. План «у клиентов» (визиты и дорога между ними) сравнивается с работой
        // по GPS-треку — ездой и стоянками от первого визита до конца последнего; паузы (разрывы трека дольше
        // 15 минут) — отдельно. С дорогой из дома — с рабочим днём из настроек (по нему же переработка).
        // Переработка по плану красит плитку красным; факт GPS в ней остаётся нейтральным (.rt-tile-fact).
        const pw = num(t.avg_plan_work_hours), ph = num(t.avg_plan_hours);
        const fw = num(t.avg_fact_work_hours), fp = num(t.avg_fact_pause_hours);
        const small = (s) => '<small>' + s + '</small>';
        const hours = (v) => fmt(v, 1) + small(NB + 'ч');
        tile('kpiDay', {
            cls: (ph !== null && dayH !== null && ph > dayH) ? 't-danger' : '',
            val: (pw === null && fw === null) ? '—'
                : small('у' + NB + 'клиентов:') + ' ' + small('план') + NB + (pw !== null ? hours(pw) : '—') + small(NB + '·') + ' '
                  + '<span class="rt-tile-fact">' + small('факт' + NB + 'по' + NB + 'GPS') + NB
                  + (fw !== null ? hours(fw) : small('нет' + NB + 'данных')) + '</span>',
            empty: pw === null && fw === null,
            sub: [fp !== null ? 'плюс паузы без визитов ≈' + NB + '<b>' + fmt(fp, 1) + NB + 'ч</b> в день' : '',
                  ph !== null ? 'с дорогой из дома — <b>' + fmt(ph, 1) + NB + 'ч</b>' + (dayH !== null ? ' из ' + fmt(dayH, 1) : '') : '']
                .filter(Boolean).join('; ') || 'нет данных',
            title: 'У клиентов — визиты и дорога между ними, от первого клиента до последнего. Факт по GPS — то же по треку: '
                + 'езда и стоянки у магазинов, в среднем за день (последние 45 дней). '
                + 'Паузы — отрезки дольше 15 минут без GPS-отметок и без визитов: перерывы, простои или выключенный телефон. '
                + 'С дорогой из дома — ещё путь к первому клиенту и домой от последнего: это сравнивается с рабочим днём из настроек.',
        });
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
        box.appendChild(mk('Все', 'все дни недели', 'all'));
        state.idx.wdList.forEach(w => {
            const b = mk(WD_SHORT[w], WD_FULL[w] + (state.idx.work.has(w) ? '' : ', вне рабочей недели'), String(w));
            if (!state.idx.work.has(w)) b.classList.add('is-off');
            box.appendChild(b);
        });
        [...state.days].forEach(w => { if (!state.idx.wdList.includes(w)) state.days.delete(w); });
        const c = d.totals.coords || {};
        $('rtCoordsNote').textContent = num(c.visits_total)
            ? 'координаты есть у ' + fmt(c.visits_with_coords) + ' из ' + fmt(c.visits_total) + ' визитов'
              + (num(c.revenue_share_with_coords) !== null ? ' · это ' + pct(c.revenue_share_with_coords) + ' выручки' : '')
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
            b.title = 'Показать на карте только клиентов: ' + mgrName(m);
            b.addEventListener('click', () => {
                state.mgr = state.mgr === String(m.agent_id) ? '' : String(m.agent_id);
                onFilter();
            });
            box.appendChild(b);
        });
        box.insertAdjacentHTML('beforeend',
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" style="background:#eef1f6;color:#0c0f14" aria-hidden="true"><i class="fas fa-warehouse"></i></span>Склад</span>' +
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" style="border:1.5px solid #a7b0c0;border-radius:50%" aria-hidden="true"><i class="fas fa-house"></i></span>Дом менеджера</span>' +
            '<span class="rt-lg rt-lg-static"><span class="rt-lg-ico" aria-hidden="true"><span class="rt-dot" style="background:#a7b0c0;width:7px;height:7px"></span></span>точка крупнее — магазин крупнее</span>');
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
            el.textContent = 'Карта не загрузилась (нет доступа к cdn.jsdelivr.net). Цифры и таблица ниже работают.';
            return;
        }
        const map = L.map('rtMap', { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
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
            : (f.noGeo ? 'нет на карте: <b>' + fmt(f.noGeo) + '</b> ' + plural(f.noGeo, 'клиент', 'клиента', 'клиентов') + ' без координат'
                : (f.pts.length ? 'все клиенты на карте' : ''));
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
            mk.bindTooltip(() => '<b>' + esc(c.name || 'Клиент') + '</b>'
                + '<br><span style="color:#a7b0c0">' + esc(visitsText(p.all)) + '</span>', { direction: 'top', offset: [0, -6] });
            mk.bindPopup(() => popupHTML(c, p.all), { maxWidth: 320 });
            mk.addTo(state.custLayer);
            bounds.push(ll);
        });
    }

    // «Иванов И.: Пн, Чт» — кто и когда ездит к клиенту
    function visitsText(visits) {
        const by = new Map();
        visits.forEach(v => {
            if (!by.has(v.mi)) by.set(v.mi, []);
            by.get(v.mi).push(dayShort(state.data.managers[v.mi].days[v.di]));
        });
        return [...by.entries()].map(([mi, ds]) => mgrName(state.data.managers[mi]) + ': ' + ds.join(', ')).join(' · ');
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
        return '<div class="rt-pop">'
            + '<div class="rt-pop-t">' + esc(c.name || 'Клиент') + '</div>'
            + '<div class="rt-pop-s">' + esc([c.code, c.area, c.group].filter(Boolean).join(' · ')) + '</div>'
            + mg + '<hr>'
            + row('Размер', SIZE[c.size] || '—')
            + row('Точка на карте', src[2])
            + row('Заказывает', opw === null ? '—' : fmt(opw, 1) + ' ' + unitWord(opw, 'раз', 'раза', 'раз') + ' в неделю')
            + row('Средний заказ', money(c.avg_order_amd) + ' · ' + fmt(c.avg_order_kg) + ' кг')
            + row('Шанс заказа зимой', pct(c.p_low))
            + row('Выручка в неделю', 'зимой ' + moneyShort(c.rev_week_low) + ' · в среднем ' + moneyShort(c.rev_week_year))
            + '</div>';
    }

    function homeMarker(m, mi) {
        const icon = L.divIcon({
            className: 'rt-pin rt-pin-home',
            html: '<span style="color:' + mgrColor(mi) + '"><i class="fas fa-house" aria-hidden="true"></i></span>',
            iconSize: [24, 24], iconAnchor: [12, 12],
        });
        return L.marker([num(m.home.lat), num(m.home.lon)], { icon, title: 'Дом: ' + mgrName(m), zIndexOffset: 500 })
            .bindTooltip(() => '<b>Дом</b> · ' + esc(mgrName(m)) + '<br><span style="color:#a7b0c0">'
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
        L.marker([num(dp.lat), num(dp.lon)], { icon, title: 'Склад', zIndexOffset: 1000 })
            .bindTooltip('<b>Склад</b> — отсюда выезжают машины', { direction: 'top', offset: [0, -12] })
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
                .bindTooltip(() => '<b>' + x.n + '.</b> ' + esc(x.c.name || 'Клиент')
                    + (erpNo !== null ? '<br><span style="color:#a7b0c0">№ в ERP: ' + fmt(erpNo) + '</span>' : ''),
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
        $('rtRouteBarText').textContent = 'Маршрут дня: ' + mgrName(s.m) + ' · ' + dayTitle(s.day);
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
            + WD_FULL[c.weekday] + (ix.W > 1 ? ', неделя ' + c.week : '') + '</span></th>';
        let h;
        if (ix.W > 1) {
            h = '<tr class="rt-heat-weeks"><th scope="col" rowspan="2" class="rt-heat-mgr">Менеджер</th>';
            for (let k = 1; k <= ix.W; k++) h += '<th scope="colgroup" colspan="' + ix.wdList.length + '">Неделя ' + k + '</th>';
            h += '<th scope="col" rowspan="2" class="rt-heat-week">В неделю</th></tr><tr>' + ix.cols.map(th).join('') + '</tr>';
        } else {
            h = '<tr><th scope="col" class="rt-heat-mgr">Менеджер</th>' + ix.cols.map(th).join('')
                + '<th scope="col" class="rt-heat-week">За неделю</th></tr>';
        }
        table.tHead.innerHTML = h;
        table.tBodies[0].innerHTML = d.managers.map(rowHTML).join('');
        markSelectedCell();
    }

    function rowHTML(m, mi) {
        const tags = [];
        if (m.included === false) tags.push('<span class="rt-badge" title="Менеджер не входит в итоги. Включить можно в настройках.">не в расчёте</span>');
        if ((m.flags || []).includes('inactive')) tags.push('<span class="rt-badge b-warn" title="Нет заказов и визитов за 8 недель — по умолчанию шаблон не входит в расчёт">без работы 8 нед.</span>');
        if (!hasHome(m)) tags.push('<span class="rt-badge b-warn" title="Дом неизвестен — день считается от первого клиента до последнего">нет дома</span>');
        if (!m.truck) tags.push('<span class="rt-badge" title="Машина не назначена — рейсы не посчитаны">нет машины</span>');
        if ((m.flags || []).includes('multi_truck')) tags.push('<span class="rt-badge" title="Менеджера возят несколько машин — считаем по самой большой">2+ машины</span>');
        const truck = m.truck ? (m.truck.car_code || m.truck.name || '')
            + (num(m.truck.capacity_kg) !== null ? ' · ' + fmt(num(m.truck.capacity_kg) / 1000, 1) + ' т' : '') : '';
        let tr = '<tr' + (m.included === false ? ' class="is-excluded"' : '') + '>'
            + '<th scope="row" class="rt-heat-mgr"><div class="rt-mgr"><span class="rt-dot" style="background:' + mgrColor(mi) + '"></span>'
            + '<div class="rt-mgr-txt"><span class="n" title="' + esc(mgrName(m)) + '">' + esc(mgrName(m)) + '</span>'
            + '<span class="c">' + esc([m.code, truck].filter(Boolean).join(' · ')) + '</span>'
            + (tags.length ? '<span class="tags">' + tags.join('') + '</span>' : '') + '</div></div></th>';
        state.idx.cols.forEach((c, ci) => {
            const di = m._byKey.get(c.week + '-' + c.weekday);
            tr += '<td>' + (di === undefined
                ? '<span class="rt-hc-none" title="В этот день по плану визитов нет">—</span>'
                : cellHTML(m, mi, m.days[di], di, ci)) + '</td>';
        });
        const wk = m.week || {};
        tr += '<td class="rt-heat-week"><span class="v">' + moneyShort(wk.revenue_low) + '</span>'
            + '<span class="s">' + fmt(wk.visits) + ' виз · ' + fmt(wk.manager_km) + ' км</span></td>';
        return tr + '</tr>';
    }

    function dayFlags(day) {
        const tr = day.truck || null, flags = day.flags || [];
        return {
            visits: num(day.visits) || 0,
            p: num(day.p_day_ge_min),
            over: flags.includes('overtime') || (num(day.overtime_minutes) || 0) > 0,
            poor: !!tr && num(tr.p_poor_trip) !== null && num(tr.p_poor_trip) >= P_POOR,
            load: !!tr && num(tr.p_overflow_peak) !== null && num(tr.p_overflow_peak) >= P_OVERFLOW,
            off: isOff(day),
        };
    }

    const statusClass = (f) => !f.visits ? 's-empty' : (f.p === null ? '' : (f.p < 0.5 ? 's-bad' : (f.p < 0.8 ? 's-mid' : 's-good')));

    function cellLabel(m, day, f) {
        const minDay = num((state.settings || {}).min_day_revenue);
        let t = mgrName(m) + ', ' + dayTitle(day) + ': ';
        if (!f.visits) return t + 'визитов нет' + (f.off ? ', день вне рабочей недели' : '') + '.';
        t += fmt(f.visits) + ' ' + plural(f.visits, 'визит', 'визита', 'визитов')
            + ', ожидаемая выручка зимой ' + money(day.revenue_low_exp)
            + ', шанс набрать ' + (minDay !== null ? fmt(minDay) + ' драм' : 'норму') + ' — ' + pct(f.p);
        if (f.over) t += '; переработка ' + hm(day.overtime_minutes);
        if (f.poor) t += '; бедный рейс';
        if (f.load) t += '; летом груз может не влезть в машину';
        if (f.off) t += '; день вне рабочей недели';
        return t + '.';
    }

    function cellHTML(m, mi, day, di, ci) {
        const f = dayFlags(day);
        const label = esc(cellLabel(m, day, f));
        const cls = 'rt-hc ' + statusClass(f) + (f.off ? ' is-off' : '');
        const attrs = ' type="button" class="' + cls + '" data-mi="' + mi + '" data-di="' + di + '" data-ci="' + ci
            + '" tabindex="-1" aria-pressed="false" aria-label="' + label + '" title="' + label + '"';
        if (!f.visits) return '<button' + attrs + '><span class="rt-hc-sub">нет визитов</span></button>';
        const icons = (f.over ? '<i class="fas fa-clock f-over"></i>' : '')
            + (f.poor ? '<i class="fas fa-truck f-poor"></i>' : '')
            + (f.load ? '<i class="fas fa-weight-hanging f-load"></i>' : '');
        return '<button' + attrs + '>'
            + '<span class="rt-hc-top"><span class="rt-hc-v">' + moneyShort(day.revenue_low_exp) + '</span>'
            + (icons ? '<span class="rt-hc-flags" aria-hidden="true">' + icons + '</span>' : '') + '</span>'
            + '<span class="rt-hc-sub">' + fmt(f.visits) + ' виз · ' + pct(f.p) + '</span></button>';
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
            '<span>Шанс набрать ' + (minDay !== null ? fmt(minDay) + ' драм' : 'дневную норму') + ' зимой:</span>'
            + item('<span class="rt-hl-sw s-bad" aria-hidden="true"></span>', 'меньше 50%')
            + item('<span class="rt-hl-sw s-mid" aria-hidden="true"></span>', '50–80%')
            + item('<span class="rt-hl-sw s-good" aria-hidden="true"></span>', '80% и больше')
            + item('<i class="fas fa-clock f-over" aria-hidden="true"></i>', 'переработка')
            + item('<i class="fas fa-truck f-poor" aria-hidden="true"></i>', 'бедный рейс')
            + item('<i class="fas fa-weight-hanging f-load" aria-hidden="true"></i>', 'летом может не влезть в машину')
            + item('<span class="rt-hl-sw" style="background:transparent;border:1px dashed #7c8698" aria-hidden="true"></span>', 'день вне рабочей недели');
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
        announce('Открыт день: ' + mgrName(m) + ', ' + dayTitle(day) + '. Маршрут показан на карте.');
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
        announce('День закрыт, на карте снова все клиенты');
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
            + '<h3>Выберите день менеджера</h3>'
            + '<p class="mb-0">Нажмите на клетку в таблице «Неделя по дням» ниже — здесь появятся клиенты этого дня в порядке объезда, '
            + 'а на карте — маршрут от дома и обратно.</p>'
            + '<div class="facts">'
            + '<span>Клиентов на карте</span><b>' + fmt(f.pts.length) + '</b>'
            + '<span>Без координат</span><b>' + fmt(f.noGeo) + '</b>'
            + '<span>Менеджеров</span><b>' + fmt(f.managers) + '</b>'
            + '<span>Визитов за цикл</span><b>' + fmt(f.visits) + '</b>'
            + '</div></div>';
    }

    function mstat(k, v, sub, cls, wide) {
        return '<div class="rt-mstat' + (wide ? ' is-wide' : '') + '"><span class="k">' + k + '</span>'
            + '<span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>'
            + (sub ? '<span class="s">' + sub + '</span>' : '') + '</div>';
    }

    // «у клиентов 5 ч 40 мин (визиты 3 ч, дорога между ними 2 ч 40 мин) · из дома и домой 50 мин»:
    // у клиентов — как факт GPS (от первого визита до последнего), весь день — с дорогой из дома
    function planSplit(m, day) {
        const work = num(day.work_minutes), commute = num(day.commute_minutes), visit = num(day.visit_minutes);
        if (work === null) return 'дорога ' + hm(day.drive_minutes) + ' · у клиентов ' + hm(day.visit_minutes);   // прежний ответ сервера
        const home = !(day.flags || []).includes('no_home') && hasHome(m);
        return 'у клиентов ' + hm(work)
            + (visit !== null ? ' (визиты ' + hm(visit) + ', дорога между ними ' + hm(work - visit) + ')' : '')
            + ' · ' + (home ? 'из дома и домой ' + hm(commute) : 'дом неизвестен — дорога из дома не посчитана');
    }

    function dayHTML(s) {
        const { m, mi, day } = s, st = state.settings || {};
        const minDay = num(st.min_day_revenue), minTrip = num(st.min_trip_revenue);
        const f = dayFlags(day), tr = day.truck || null, d = state.data;
        const badge = (cls, ico, txt) => '<span class="rt-badge ' + cls + '"><i class="fas ' + ico + '" aria-hidden="true"></i>' + txt + '</span>';
        const badges = [];
        if (f.over) badges.push(badge('b-danger', 'fa-clock', 'переработка ' + hm(day.overtime_minutes)));
        if (f.poor) badges.push(badge('b-warn', 'fa-truck', 'бедный рейс · шанс ' + pct(tr.p_poor_trip)));
        if (f.load) badges.push(badge('b-warn', 'fa-weight-hanging', 'летом может не влезть · ' + pct(tr.p_overflow_peak)));
        if ((day.flags || []).includes('no_home') || !hasHome(m)) badges.push(badge('', 'fa-house', 'дом неизвестен — от первого клиента'));
        if (f.off) badges.push(badge('', 'fa-moon', 'день вне рабочей недели'));
        if ((day.flags || []).includes('sunday_order')) badges.push(badge('', 'fa-calendar-day', 'заказ воскресенья — доставка в понедельник'));

        const pcls = f.p === null ? '' : (f.p < 0.5 ? 'danger' : (f.p < 0.8 ? 'warn' : 'ok'));
        const chanceWords = f.p === null ? '' : (f.p < 0.5 ? 'скорее не наберёт' : (f.p < 0.8 ? 'может не набрать' : 'скорее наберёт'));
        let stats = mstat('Выручка зимой', money(day.revenue_low_exp),
            (num(day.revenue_low_p10) !== null && num(day.revenue_low_p90) !== null)
                ? 'в 8 днях из 10: от ' + moneyShort(day.revenue_low_p10) + ' до ' + moneyShort(day.revenue_low_p90) : '')
            + mstat('Шанс набрать ' + (minDay !== null ? fmt(minDay) : 'норму'), pct(f.p), chanceWords, pcls)
            + mstat('Визитов', fmt(f.visits), num(day.visits_no_coords) ? fmt(day.visits_no_coords) + ' без координат' : 'все с координатами')
            + mstat('Пробег менеджера', fmt(day.manager_km, 1) + ' км',
                [num(day.manager_liters) !== null ? fmt(day.manager_liters, 1) + ' л топлива' : '',
                 num(day.manager_km_rownum) !== null ? 'по порядку ERP было бы ' + fmt(day.manager_km_rownum, 1) + NB + 'км' : '']
                    .filter(Boolean).join(' · '))
            + mstat('План дня', hm(day.plan_minutes), planSplit(m, day), f.over ? 'danger' : '', true);
        const fact = m.fact || null;
        if (fact && num(fact.days) > 0) {
            const parts = [];
            const planWork = num((m.week || {}).avg_work_hours);
            const work = num(fact.work_hours), pause = num(fact.pause_hours), trackDays = num(fact.track_days);
            if (work !== null) {
                // план «у клиентов» — против работы по треку (езда и стоянки); паузы — отдельно, как в плитке
                parts.push('у клиентов ' + fmt(work, 1) + NB + 'ч'
                    + (planWork !== null ? ' (по плану ' + fmt(planWork, 1) + NB + 'ч)' : '')
                    + (pause !== null ? ', плюс паузы без визитов ≈' + NB + fmt(pause, 1) + NB + 'ч' : ''));
            } else if (num(fact.hours) !== null) {
                parts.push('от первого визита до конца последнего ' + fmt(fact.hours, 1) + NB + 'ч — трека нет, паузы не выделены');
            }
            if (num(fact.visits_per_day) !== null) parts.push(fmt(fact.visits_per_day, 1) + NB + unitWord(fact.visits_per_day, 'визит', 'визита', 'визитов'));
            if (num(fact.productive_share) !== null) parts.push('с заказом ' + pct(fact.productive_share));
            // работа и паузы — по дням с треком (он короче: 45 дней против 8 недель)
            parts.push('за ' + fmt(fact.days) + NB + 'дн.'
                + (work !== null && trackDays !== null && trackDays < num(fact.days) ? ' (по треку — ' + fmt(trackDays) + NB + 'дн.)' : ''));
            stats += mstat('Обычный день по GPS', (fact.day_start && fact.day_end) ? esc(fact.day_start) + '–' + esc(fact.day_end) : '—', parts.join(' · '), '', true);
        }
        if (tr) {
            const trips = num(tr.trips_exp);
            stats += mstat('Машина' + (day.delivery_label ? ' · доставка ' + esc(day.delivery_label) : ''),
                (trips === null ? '—' : fmt(trips, 1) + ' ' + unitWord(trips, 'рейс', 'рейса', 'рейсов')) + ' · ' + fmt(tr.km_exp, 1) + ' км',
                fmt(tr.liters_exp, 1) + ' л дизеля · заказов не будет: ' + pct(tr.p_no_trip)
                    + ' · летом загрузка ' + (num(tr.load_pct_peak) === null ? '—' : fmt(tr.load_pct_peak) + '%')
                    + (f.poor && minTrip !== null ? ' · меньше ' + fmt(minTrip) + ' драм: ' + pct(tr.p_poor_trip) : ''),
                '', true);
        } else {
            const noDepot = !d.depot || num(d.depot.lat) === null;
            stats += mstat('Машина', '—', noDepot ? 'рейс не посчитан: ' + setLink('depot', 'укажите склад')
                : 'рейс не посчитан: ' + setLink('trucks', 'назначьте машину'), '', true);
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
                + '<td class="l nm"><span class="n">' + esc(c ? (c.name || 'Клиент') : 'Клиент ' + id) + '</span>'
                + '<span class="c">' + esc(c ? (c.code || '') : String(id)) + '</span>'
                + '<span class="rt-dl-meta">' + badge + '<span>' + size + '</span></span></td>'   // телефон: вместо двух колонок
                + '<td class="l rt-dl-wide">' + badge + '</td>'
                + '<td class="l rt-dl-wide">' + size + '</td>'
                + '<td>' + pct(c && c.p_low) + '</td>'
                + '<td>' + (rev === null ? '—' : fmt(Math.round(rev))) + '</td>'
                + '<td>' + fmt(c && c.avg_order_kg) + '</td></tr>';
        }).join('');
        const noGeo = stops.filter(st => !stopGeo(st)).length;
        const list = stops.length
            ? '<div class="rt-daylist-wrap"><table class="rt-daylist"><caption class="rt-sr-only">Клиенты дня в порядке объезда</caption><thead><tr>'
              + '<th scope="col" class="wrap" title="Порядок объезда — самый короткий внутри дня">№ объезда</th>'
              + '<th scope="col" class="wrap" title="Порядок в шаблоне маршрута ERP">№ в ERP</th>'
              + '<th scope="col" class="l">Клиент</th><th scope="col" class="l rt-dl-wide">Точка</th><th scope="col" class="l rt-dl-wide">Размер</th>'
              + '<th scope="col" title="Шанс, что клиент закажет при визите зимой">Шанс</th>'
              + '<th scope="col" title="Ожидаемая выручка этого визита зимой, драм">Выручка</th>'
              + '<th scope="col" title="Средний вес заказа за год">Кг</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
              + '<p class="rt-day-note mt-2 mb-0">' + ORDER_NOTE + ' Шанс и выручка — зимние: шанс заказа при визите × средний зимний заказ. Кг — средний заказ за год.</p>'
            : '<p class="rt-empty px-0">В этот день по плану визитов нет.</p>';

        return '<div class="rt-day" id="rtDay">'
            + '<div class="rt-day-head"><span class="rt-dot" style="background:' + mgrColor(mi) + '"></span>'
            + '<div class="rt-day-titles"><h3 class="rt-day-title" id="rtDayTitle" tabindex="-1">'
            + esc(mgrName(m)) + ' · ' + esc(dayTitle(day)) + '</h3>'
            + '<div class="rt-day-sub">' + (day.delivery_label ? 'доставка: ' + esc(day.delivery_label) + ' · ' : '')
            + (hasHome(m) ? 'от дома и обратно' : 'дом неизвестен') + '</div></div>'
            + '<button type="button" class="rt-iconbtn" id="rtDayClose" aria-label="Закрыть день и вернуться к таблице">'
            + '<i class="fas fa-xmark" aria-hidden="true"></i></button></div>'
            + (badges.length ? '<div class="rt-day-flags">' + badges.join('') + '</div>' : '')
            + '<div class="rt-day-stats">' + stats + '</div>'
            + (noGeo ? '<p class="rt-day-note"><i class="fas fa-location-crosshairs" aria-hidden="true"></i>'
                + fmt(noGeo) + ' ' + plural(noGeo, 'клиент', 'клиента', 'клиентов') + ' без координат — на карте их нет, линия идёт мимо.</p>' : '')
            + list
            + '<div class="rt-day-actions"><button type="button" class="rt-btn rt-btn-ghost" id="rtDayBack">'
            + '<i class="fas fa-arrow-left" aria-hidden="true"></i>К таблице</button></div>'
            + '</div>';
    }

    // ---------- Легенда сезонов ----------
    // Действующие нормы дорог и визитов и откуда они: число из настроек, калибровка по GPS или по умолчанию
    function normsItems(norms) {
        const src = (n) => NORM_SRC[n.source] || 'источник неизвестен';
        const item = (key, label, d, unit) => {
            const n = norms[key];
            if (!n || num(n.value) === null) return null;
            return label + ' ' + fmt(n.value, d) + unit + ' — ' + src(n);
        };
        const sizes = [['small', 'мелкий'], ['medium', 'средний'], ['large', 'крупный']]
            .map(([k, label]) => ({ label, n: norms['visit_min_' + k] }));
        let visit = null;
        if (sizes.every(x => x.n && num(x.n.value) !== null)) {
            const same = sizes.every(x => x.n.source === sizes[0].n.source);
            visit = 'визит у клиента: ' + sizes.map(x => x.label + ' ' + fmt(x.n.value, 1) + (same ? '' : ' (' + src(x.n) + ')')).join(' · ')
                + NB + 'мин' + (same ? ' — ' + src(sizes[0].n) : '');
        }
        return [
            item('detour_factor', 'извилистость дорог', 2, ''),
            item('speed_city_kmh', 'скорость в городе', 1, NB + 'км/ч'),
            item('speed_region_kmh', 'скорость по области', 1, NB + 'км/ч'),
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
            card('is-low', 'fa-snowflake', 'Зима — низкий сезон', monthsText(low), [
                'выручка дня и шанс набрать ' + (minDay !== null ? fmt(minDay) + ' драм' : 'норму дня'),
                'слабые дни в таблице',
                'бедные рейсы (меньше ' + (minTrip !== null ? fmt(minTrip) + ' драм' : 'нормы рейса') + ')',
            ])
            + card('is-peak', 'fa-sun', 'Лето — пик', monthsText(peak), [
                'загрузка машин',
                'риск, что груз не влезет в машину',
            ])
            + card('is-year', 'fa-calendar', 'Весь год', 'в среднем за 12 месяцев', [
                'дизель и пробег грузовиков',
                'число рейсов',
                'выручка клиента «в среднем»',
            ])
            + card('is-plan', 'fa-route', 'Маршрут дня', 'от сезона не зависит', [
                'пробег и топливо менеджеров',
                'часы рабочего дня',
                'клиенты — из шаблонов ERP, порядок объезда — самый короткий внутри дня',
            ].concat(normsItems(state.data.norms)));
        $('rtSeasonSrc').textContent = se.source === 'manual'
            ? 'Месяцы сезонов заданы вручную в настройках.'
            : (se.source === 'mixed'
                ? 'Часть месяцев сезонов задана вручную в настройках, остальные найдены автоматически по продажам.'
                : 'Месяцы сезонов найдены автоматически: зима — где продажи заметно ниже среднего месяца, лето — заметно выше (по трём последним годам). Поменять можно в настройках.');
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

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        loadFilter();
        syncNavOffset();
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
        load(false);
    });
})();

/* «Маршруты · оптимизация» /routes/optimize — этап 3, режим А: магазины остаются у своих менеджеров,
   программа предлагает дни визитов и частоту.
   Контракт — docs/plans/stage-3-plan.md §10 и §10.1:
     POST /api/routes/optimize → job_id (409 — расчёт уже идёт, в ответе job_id идущей задачи);
     опрос GET /api/routes/optimize/<job_id> раз в 1,5 с; GET /api/routes/optimize/last (404 — расчётов
     ещё не было); POST /api/routes/decisions — одно решение или {items: [...]} одной транзакцией,
     {action: 'reset_all'}; GET /api/routes/decisions — «Ваши решения» и число изменений для Excel;
     GET /api/routes/plan-export — строки плана, Excel собирается здесь (SheetJS, глобальный XLSX).
   Галочки менеджеров, дома и склад — из GET /api/routes/overview, порог дня — из GET /api/routes/settings.
   Безопасность: строки из ERP и из ответов сервера выводятся только текстом — узлы собирает h()
   через textContent; в подсказки и попапы Leaflet уходят готовые DOM-узлы, а не HTML-строки. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const NB = '\u00a0';     // неразрывный пробел: число не отрывается от единицы
    const MINUS = '\u2212';

    const WD_SHORT = { 1: 'Пн', 2: 'Вт', 3: 'Ср', 4: 'Чт', 5: 'Пт', 6: 'Сб', 7: 'Вс' };
    const WD_FULL = { 1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота', 7: 'воскресенье' };
    // Цвет точки на карте — день недели. Оттенки из палитры менеджеров обзора (контраст к тёмной карте ≥ 3:1)
    const WD_COLORS = { 1: '#18c1fc', 2: '#fe904d', 3: '#b0a2ff', 4: '#b8b90c', 5: '#14cfa3', 6: '#fe80c0', 7: '#8b93a7' };
    // Цвета менеджеров — как в обзоре: слот по позиции менеджера в ответе
    const MGR_COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3',
                        '#0d9298', '#b8b90c', '#ae55c1', '#37981b', '#fe80c0', '#d64651'];
    const MGR_OTHER = '#8b93a7';
    const TYPE_TEXT = { move: 'перенос', frequency: 'частота', both: 'перенос и частота' };
    // Какие решения отправляет изменение: у «both» — два, шаблон дней и частота
    const KINDS = { move: ['pattern'], frequency: ['freq'], both: ['pattern', 'freq'] };
    const ACTION_STATUS = { accept: 'accepted', reject: 'rejected', reset: null };
    const STATUS = {
        none: { cls: 'b-none', icon: 'fa-circle-question', text: 'без решения' },
        accepted: { cls: 'b-ok', icon: 'fa-lock', text: 'принято' },
        rejected: { cls: '', icon: 'fa-ban', text: 'отклонено' },
        mixed: { cls: 'b-warn', icon: 'fa-circle-half-stroke', text: 'частично' },
    };
    // Эффект изменения, применённого к текущему плану — в неделю; меньше — лучше
    const EFFECTS = [
        { key: 'manager_km_week', label: 'менеджер', unit: NB + 'км', d: 1, title: 'Пробег менеджера за неделю' },
        { key: 'truck_km_week', label: 'грузовик', unit: NB + 'км', d: 1, title: 'Пробег грузовика за неделю' },
        { key: 'weak_days_week', label: 'слабые дни', unit: '', d: 2, title: 'Ожидаемое число слабых дней зимой за неделю' },
        { key: 'minutes_week', label: 'время', unit: NB + 'мин', d: 0, title: 'Время менеджера за неделю' },
    ];
    const HINT_ICON = { freq_up: 'fa-arrow-trend-up', no_orders: 'fa-circle-exclamation' };
    const START_HINT = {
        current: 'За основу — текущие дни из ERP: программа меняет только то, что даёт выгоду. Изменений будет немного.',
        fresh: 'Клиенты раскладываются по дням заново, как будто плана нет. Изменений будет намного больше — так виден предел экономии.',
    };
    const FREQ_HINT = {
        sales: 'Частота только снижается — там, где клиент заказывает реже, чем его посещают. Повысить частоту программа лишь подскажет.',
        current: 'Сколько раз в неделю посещать клиента — как сейчас в ERP; меняются только дни.',
    };
    const RUN_NOTE = 'обычно 1–2 минуты · ERP только читается, в ERP ничего не записывается';
    const PLAN_HEAD = ['Код менеджера', 'Менеджер', 'Неделя цикла', 'День', '№', 'Код клиента', 'Клиент', 'Отметка'];
    const PLAN_COLS = [14, 28, 13, 7, 6, 14, 42, 18];
    const CHANGE_HEAD = ['Код менеджера', 'Менеджер', 'Код клиента', 'Клиент', 'Что меняется', 'Было', 'Стало', 'Причина',
                         'Км менеджера в неделю', 'Км грузовика в неделю', 'Слабых дней в неделю', 'Минут в неделю'];
    const CHANGE_COLS = [14, 28, 14, 42, 18, 26, 26, 46, 12, 12, 12, 10];
    const POLL_MS = 1500;
    const POLL_MAX_MS = 15000;
    const JOB_TTL_MS = 3 * 3600 * 1000;      // сохранённую задачу старше 3 часов не подхватываем
    const OPEN_ALL_MAX = 40;                 // столько предложений и меньше — все менеджеры раскрыты сразу
    const STALE_LIST_MAX = 12;               // устаревших решений в предупреждении — не больше
    const LS_JOB = 'routesOptimizeJob';
    const LS_DIRTY = 'routesOptimizeDirty';
    const YEREVAN = [40.1792, 44.4991];
    const RM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ---------- Форматирование ----------
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => {
        const n = num(v);
        return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d });
    };
    const round = (v, d) => { const k = 10 ** d; return Math.round(v * k) / k; };
    const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
    const obj = (v) => (isObj(v) ? v : {});
    const arr = (v) => (Array.isArray(v) ? v : []);
    const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
    const norm = (s) => String(s ?? '').toLowerCase().replace(/ё/g, 'е');
    const pct = (p) => (num(p) === null ? '—' : Math.round(num(p) * 100) + '%');
    // «+1,2» / «−0,8» / «0» — знак после округления до d знаков
    function signed(v, d) {
        const n = num(v);
        if (n === null) return '—';
        const r = round(n, d);
        if (r === 0) return '0';
        return (r > 0 ? '+' : MINUS) + fmt(Math.abs(r), d);
    }
    // −1 — стало меньше, +1 — больше, 0 — без изменений (после округления до d знаков)
    function trend(v, d) {
        const n = num(v);
        if (n === null) return 0;
        const r = round(n, d);
        return r < 0 ? -1 : (r > 0 ? 1 : 0);
    }
    function moneyShort(v) {
        const n = num(v);
        if (n === null) return '—';
        const a = Math.abs(n);
        if (a >= 1e6) return fmt(n / 1e6, 1) + NB + 'млн';
        if (a >= 1e3) return fmt(Math.round(n / 1e3)) + NB + 'тыс.';
        return fmt(Math.round(n));
    }
    function plural(n, one, few, many) {
        const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
        if (a > 10 && a < 20) return many;
        if (b > 1 && b < 5) return few;
        if (b === 1) return one;
        return many;
    }
    // «из 6 дней», «из 21 дня» — родительный падеж после «из N»
    const genDays = (n) => (n % 10 === 1 && n % 100 !== 11) ? 'дня' : 'дней';
    function hm(minutes) {
        const n = num(minutes);
        if (n === null) return '—';
        const m = Math.max(0, Math.round(n)), hh = Math.floor(m / 60), r = m % 60;
        return hh ? (r ? hh + NB + 'ч ' + r + NB + 'мин' : hh + NB + 'ч') : r + NB + 'мин';
    }
    function duration(sec) {
        const n = num(sec);
        if (n === null) return '';
        const s = Math.max(0, Math.round(n)), m = Math.floor(s / 60), r = s % 60;
        return m ? m + NB + 'мин' + (r ? ' ' + r + NB + 'с' : '') : r + NB + 'с';
    }
    const clock = (ms) => {
        const s = Math.max(0, Math.floor(ms / 1000));
        return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    };
    function parseTime(s) {
        if (typeof s !== 'string' || !s) return null;
        const t = new Date(s);
        return Number.isNaN(t.getTime()) ? null : t;
    }
    const stamp = (t) => t.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    const ymd = (t) => t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');

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
    const spin = () => h('span', { class: 'rt-spin-inline', 'aria-hidden': 'true' });

    // ---------- Состояние ----------
    const state = {
        managers: null,        // из обзора: [{agent_id, code, name, included}] — галочки параметров
        homes: new Map(),      // agent_id → [lat, lon] — дома из обзора
        depot: null,           // [lat, lon] — склад из обзора
        settings: null,        // настройки: порог дня для текстов
        pick: new Set(),       // выбранные менеджеры (agent_id строкой)
        result: null,          // результат расчёта (§10.1)
        entries: new Map(),    // «агент:клиент» → строка предложения
        groups: [],            // группы менеджеров в списке предложений
        open: new Set(),       // раскрытые группы (agent_id строкой)
        filter: { type: 'all', q: '' },
        dirty: false,          // после расчёта менялись решения — пора пересчитать
        lastDecision: 0,       // когда на этой странице последний раз сохранилось решение (мс)
        job: null,             // {id, timer, inflight, fails, started, resumed, alive}
        starting: false,
        tick: null,
        lastRun: null,         // параметры последнего запуска — для «Повторить»
        retry: null,
        map: null, mapFailed: false, layers: null,
        mapAgent: null, mapMode: 'after', mapDay: null,
        exporting: false,
        decisions: null,       // GET /api/routes/decisions: {data_as_of, summary, decisions}
        decError: '',          // список решений не загрузился
        decSeq: 0,             // номер запроса списка: ответ старого запроса не перезаписывает новый
        decBusy: false,        // идёт «Сбросить все» или отмена решения из списка
        decOpen: false,        // список «Ваши решения» раскрыт
    };

    // Липкое меню дашборда переносится на 2–3 строки — прокрутка к разделам учитывает его высоту
    function syncNavOffset() {
        const nav = document.querySelector('.navbar.sticky-top');
        $('rtOptimize').style.setProperty('--rt-nav-h', (nav ? Math.ceil(nav.getBoundingClientRect().height) : 0) + 'px');
    }

    function announce(msg) {
        const el = $('roStatus');
        el.textContent = '';
        setTimeout(() => { el.textContent = msg; }, 40);
    }

    function scrollTo(el) {
        el.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'start' });
    }

    function debounce(fn, ms) {
        let t = null;
        return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
    }

    // ---------- Сервер ----------
    const HTTP_TEXT = {
        400: 'Сервер не принял запрос.', 401: 'Требуется вход в систему.',
        403: 'Доступ запрещён — раздел только для администратора.', 404: 'Не найдено.',
        409: 'Расчёт уже идёт.', 415: 'Сервер не принял запрос.',
        500: 'Внутренняя ошибка сервера.', 503: 'База данных ERP недоступна.',
    };

    async function api(method, url, body) {
        const opts = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let resp;
        try {
            resp = await fetch(url, opts);
        } catch (e) {
            throw Object.assign(new Error('Нет связи с сервером.'), { network: true, status: 0, data: null });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (resp.ok && isObj(data) && data.success === true) return data;
        const d = isObj(data) ? data : null;
        const msg = (d && typeof d.error === 'string' && d.error.trim())
            || (d && isObj(d.errors) ? Object.values(d.errors).map(String).join('; ') : '')
            || HTTP_TEXT[resp.status] || ('Ошибка сервера (код ' + resp.status + ').');
        throw Object.assign(new Error(msg), { status: resp.status, data: d });
    }
    const sentence = (s) => { const t = String(s || '').trim(); return !t || /[.!?…]$/.test(t) ? t : t + '.'; };

    // ---------- 01 · Параметры ----------
    const mgrName = (m) => (m && m.name ? String(m.name) : 'Менеджер ' + (m ? (m.code || m.agent_id) : ''));

    function renderHints() {
        const val = (name) => (document.querySelector('input[name="' + name + '"]:checked') || {}).value;
        $('roStartHint').textContent = START_HINT[val('roStart')] || '';
        $('roFreqHint').textContent = FREQ_HINT[val('roFreq')] || '';
    }

    // Менеджеры вне расчёта (галочка «в расчёте» в настройках) выбрать нельзя — сервер их не посчитает;
    // рядом — ссылка на настройки, где их включают
    const pickable = () => (state.managers || []).filter(m => m.included);

    function renderPicker() {
        const box = $('roMgrs');
        box.textContent = '';
        $('roMgrs').parentElement.querySelector('.ro-quick').hidden = !pickable().length;
        if (!state.managers.length) {
            box.append(h('span', { class: 'ro-muted', text: 'В ERP нет менеджеров с маршрутами.' }));
        } else if (!pickable().length) {
            box.append(h('span', { class: 'ro-muted' }, 'Ни один менеджер не в расчёте — ',
                h('a', { href: '/routes/settings#managers', text: 'включите их в настройках' }), '.'));
        }
        state.managers.forEach((m, i) => {
            const id = 'roPick' + i;
            const txt = h('span', { class: 't' }, h('span', { class: 'n', text: mgrName(m), title: mgrName(m) }),
                m.code ? h('span', { class: 'c', text: String(m.code) }) : null);
            if (m.included) {
                box.append(h('label', { class: 'ro-mgr-item', for: id },
                    h('input', { type: 'checkbox', id, checked: state.pick.has(String(m.agent_id)), dataset: { i: String(i) } }),
                    txt));
                return;
            }
            txt.append(h('span', { class: 'x', id: id + 'x' }, 'не в расчёте · ',
                h('a', { href: '/routes/settings#managers', text: 'включить в настройках' })));
            box.append(h('div', { class: 'ro-mgr-item is-off' },
                h('input', { type: 'checkbox', id, disabled: true, 'aria-describedby': id + 'x',
                    'aria-label': mgrName(m) + ' — не в расчёте' }),
                txt));
        });
        renderPickCount();
    }

    function renderPickCount() {
        const n = pickable().length, off = state.managers ? state.managers.length - n : 0;
        $('roMgrCount').textContent = n ? 'выбрано ' + state.pick.size + ' из ' + n + (off ? ' · не в расчёте ' + off : '') : '';
        updateRunState();
    }

    function setPick(mode) {
        if (!state.managers) return;
        state.pick = new Set(mode === 'all' ? pickable().map(m => String(m.agent_id)) : []);
        $('roMgrs').querySelectorAll('input[type="checkbox"]:not(:disabled)').forEach(inp => {
            inp.checked = state.pick.has(String(state.managers[+inp.dataset.i].agent_id));
        });
        renderPickCount();
    }

    function onPick(e) {
        const inp = e.target;
        if (!inp.matches('input[type="checkbox"]') || inp.disabled || !state.managers) return;
        const m = state.managers[+inp.dataset.i];
        if (!m || !m.included) return;
        if (inp.checked) state.pick.add(String(m.agent_id));
        else state.pick.delete(String(m.agent_id));
        renderPickCount();
    }

    function readParams() {
        const val = (name, def) => { const el = document.querySelector('input[name="' + name + '"]:checked'); return el ? el.value : def; };
        return {
            // список не загрузился — null: сервер возьмёт менеджеров «в расчёте» из настроек
            agent_ids: state.managers ? pickable().filter(m => state.pick.has(String(m.agent_id))).map(m => m.agent_id) : null,
            start: val('roStart', 'current') === 'fresh' ? 'fresh' : 'current',
            frequencies: val('roFreq', 'sales') === 'current' ? 'current' : 'sales',
        };
    }

    // Параметры результата — в форму (перед «Пересчитать с учётом решений»); менеджер, которого с тех пор
    // исключили из расчёта, не выбирается
    function setParams(p) {
        const check = (name, v) => document.querySelectorAll('input[name="' + name + '"]').forEach(r => { r.checked = r.value === v; });
        check('roStart', p.start);
        check('roFreq', p.frequencies);
        if (state.managers && Array.isArray(p.agent_ids)) {
            const ok = new Set(pickable().map(m => String(m.agent_id)));
            state.pick = new Set(p.agent_ids.map(String).filter(id => ok.has(id)));
            renderPicker();
        }
        renderHints();
    }

    const running = () => !!state.job || state.starting;

    function updateRunState() {
        const busy = running();
        const none = !!state.managers && state.pick.size === 0;
        const btn = $('roRunBtn');
        btn.setAttribute('aria-disabled', busy || none ? 'true' : 'false');
        btn.setAttribute('aria-busy', busy ? 'true' : 'false');
        btn.querySelector('i').className = busy ? 'rt-spin-inline' : 'fas fa-play';
        btn.querySelector('span').textContent = busy ? 'Идёт расчёт…' : 'Рассчитать';
        const note = $('roRunNote');
        note.textContent = busy ? 'можно уйти со страницы — расчёт продолжится на сервере'
            : (none ? 'Выберите хотя бы одного менеджера' : RUN_NOTE);
        note.classList.toggle('is-warn', none && !busy);
        ['roMgrFs', 'roStartFs', 'roFreqFs'].forEach(id => { $(id).disabled = busy; });
        $('roRecalcBtn').setAttribute('aria-disabled', busy ? 'true' : 'false');
        $('roStaleNote').classList.toggle('d-none', !(busy && state.result));
    }

    // ---------- Запуск и опрос задачи ----------
    function run(params) {
        if (running()) { announce('Расчёт уже идёт — дождитесь окончания'); return; }
        if (Array.isArray(params.agent_ids) && !params.agent_ids.length) {
            announce('Выберите хотя бы одного менеджера');
            const first = $('roMgrs').querySelector('input');
            if (first) first.focus();
            return;
        }
        start(params);
    }

    async function start(params) {
        state.starting = true;
        state.lastRun = params;
        hideRunError();
        showRunning('Запускаю расчёт…');
        try {
            const d = await api('POST', '/api/routes/optimize', params);
            const id = d.job_id;
            if ((typeof id !== 'string' || !id) && typeof id !== 'number') throw Object.assign(new Error('Сервер не вернул номер задачи.'), { status: 200 });
            state.starting = false;
            attach(String(id), false);
            announce('Расчёт запущен');
        } catch (e) {
            state.starting = false;
            const busyId = e.status === 409 && e.data ? (e.data.job_id ?? obj(e.data.job).id) : null;
            if (busyId !== null && busyId !== undefined && busyId !== '') {
                attach(String(busyId), false);   // уже идёт — показываем ход той задачи
                announce('Расчёт уже идёт — показываю его ход');
                return;
            }
            hideRunning();
            if (e.status === 409) {
                showRunError('Расчёт уже идёт — его запустили в другой вкладке или другой пользователь. Дождитесь окончания и нажмите «Проверить снова».',
                    { retryText: 'Проверить снова', retry: () => loadLast(true, true) });
                return;
            }
            const why = e.network ? 'нет связи с сервером. Проверьте сеть и нажмите «Повторить».'
                : (e.status === 503 ? 'база данных ERP недоступна. Попробуйте чуть позже.' : sentence(e.message));
            showRunError('Расчёт не запущен: ' + why, { auth: e.status === 401, retry: () => run(params) });
        }
    }

    function forgetJob() {
        try { localStorage.removeItem(LS_JOB); } catch (e) { /* нет доступа к localStorage */ }
    }

    // Задача, запущенная до перезагрузки страницы: если ещё идёт — показываем её ход
    function resumeJob() {
        let saved = null;
        try { saved = JSON.parse(localStorage.getItem(LS_JOB) || 'null'); } catch (e) { saved = null; }
        if (isObj(saved) && typeof saved.id === 'string' && saved.id && Date.now() - (num(saved.at) || 0) < JOB_TTL_MS) {
            attach(saved.id, true, num(saved.at));
        } else {
            forgetJob();
        }
    }

    // resumed — задача из прошлого визита страницы: её 404 молча забываем, фокус по готовности не трогаем
    function attach(id, resumed, startedAt) {
        stopJob();
        const started = startedAt || Date.now();
        state.job = { id, timer: null, inflight: false, fails: 0, started, resumed, alive: false };
        try { localStorage.setItem(LS_JOB, JSON.stringify({ id, at: started })); } catch (e) { /* приватный режим */ }
        showRunning(resumed ? 'Проверяю ход расчёта…' : 'Готовлю данные…');
        poll();
    }

    function stopJob() {
        if (state.job && state.job.timer) clearTimeout(state.job.timer);
        state.job = null;
    }

    function endJob() {
        stopJob();
        forgetJob();
        hideRunning();
    }

    async function poll() {
        const job = state.job;
        if (!job || job.inflight) return;   // один запрос за раз: «Проверить сейчас» не плодит второй опрос
        clearTimeout(job.timer);
        job.timer = null;
        job.inflight = true;
        let d;
        try {
            d = await api('GET', '/api/routes/optimize/' + encodeURIComponent(job.id));
        } catch (e) {
            job.inflight = false;
            if (state.job !== job) return;
            if (e.network || e.status >= 500) {
                job.fails++;
                const wait = Math.min(POLL_MS * 2 ** job.fails, POLL_MAX_MS);
                showNet((e.network ? 'Нет связи с сервером' : 'Сервер не ответил') + ' — проверю ход расчёта снова через '
                    + Math.round(wait / 1000) + NB + 'с. Расчёт на сервере при этом не прерывается.');
                job.timer = setTimeout(poll, wait);
                return;
            }
            const quiet = job.resumed && !job.alive && e.status === 404;
            endJob();
            if (quiet) return;   // сохранённая задача давно закончилась — просто забываем её
            showRunError(e.status === 404
                ? 'Задача расчёта не найдена — возможно, сервер перезапускался. Запустите расчёт заново.'
                : 'Не удалось узнать ход расчёта: ' + sentence(e.message), { auth: e.status === 401 });
            return;
        }
        job.inflight = false;
        if (state.job !== job) return;
        job.fails = 0;
        hideNet();
        const info = obj(d.job);
        if (info.status === 'done') {
            endJob();
            if (isObj(d.result)) acceptResult(d.result, !job.resumed, job.started);
            else loadLast(true, !job.resumed, job.started);
        } else if (info.status === 'error') {
            endJob();
            showRunError('Расчёт не удался. ' + sentence(typeof info.error === 'string' && info.error ? info.error : 'Попробуйте ещё раз.'),
                { retry: () => run(state.lastRun || readParams()) });
        } else {
            job.alive = true;
            showProgress(obj(d.progress));
            job.timer = setTimeout(poll, POLL_MS);
        }
    }

    function showRunning(text) {
        $('roProgress').classList.remove('d-none');
        $('roProgressText').textContent = text;
        setBar(0, text);
        hideNet();
        const t0 = Date.now();
        clearInterval(state.tick);
        const upd = () => { $('roProgressTime').textContent = clock(Date.now() - (state.job ? state.job.started : t0)); };
        upd();
        state.tick = setInterval(upd, 1000);
        updateRunState();
    }

    function hideRunning() {
        clearInterval(state.tick);
        state.tick = null;
        $('roProgress').classList.add('d-none');
        updateRunState();
    }

    // «Менеджер 4 из 9 — A003/9 · Имя»: done — сколько менеджеров уже посчитано, agent_code — тот, что считается сейчас
    function showProgress(p) {
        const done = num(p.done), total = num(p.total);
        const code = p.agent_code === null || p.agent_code === undefined ? '' : String(p.agent_code);
        let text;
        if (total === null || total <= 0) {
            text = 'Готовлю данные ERP…';
        } else if (done !== null && done >= total) {
            text = 'Итоговая оценка «было → стало»…';
        } else {
            const k = Math.min(total, Math.max(0, done || 0) + 1);
            const who = typeof p.agent_name === 'string' && p.agent_name ? p.agent_name
                : ((code && state.managers && state.managers.find(m => String(m.code) === code)) || {}).name;
            text = 'Менеджер ' + k + ' из ' + total + (code ? ' — ' + code : '') + (who ? ' · ' + who : '');
        }
        const el = $('roProgressText');
        if (el.textContent !== text) el.textContent = text;   // живой регион объявляет только смену менеджера
        setBar(total > 0 ? Math.round(Math.min(1, Math.max(0, done || 0) / total) * 100) : 0, text);
    }

    function setBar(p, text) {
        $('roBarFill').style.width = p + '%';
        const bar = $('roBar');
        bar.setAttribute('aria-valuenow', String(p));
        bar.setAttribute('aria-valuetext', p + '% — ' + text);
    }

    function showNet(text) {
        $('roNetText').textContent = text;
        $('roNet').classList.remove('d-none');
    }
    function hideNet() { $('roNet').classList.add('d-none'); }

    function showRunError(text, o) {
        $('roRunErrorText').textContent = text;
        $('roLoginLink').classList.toggle('d-none', !o.auth);
        const btn = $('roRunRetry');
        btn.classList.toggle('d-none', !!o.auth);
        btn.textContent = o.retryText || 'Повторить';
        state.retry = o.retry || (() => run(state.lastRun || readParams()));
        $('roRunError').classList.remove('d-none');
    }
    function hideRunError() { $('roRunError').classList.add('d-none'); }

    function recalc() {
        if (running()) { announce('Расчёт уже идёт — дождитесь окончания'); return; }
        if ([...state.entries.values()].some(e => e.saving)) { announce('Дождитесь, пока сохранятся решения'); return; }
        const p = state.result ? state.result.params : {};
        const params = {
            agent_ids: Array.isArray(p.agent_ids) ? p.agent_ids : null,
            start: p.start === 'fresh' ? 'fresh' : 'current',
            frequencies: p.frequencies === 'current' ? 'current' : 'sales',
        };
        setParams(params);
        if (state.managers && params.agent_ids) params.agent_ids = readParams().agent_ids;   // без исключённых с тех пор
        if (Array.isArray(params.agent_ids) && !params.agent_ids.length) {
            announce('Менеджеры этого расчёта больше не в расчёте — выберите менеджеров');
            return;
        }
        start(params);
        const box = $('roProgress');
        scrollTo(box);
        box.focus({ preventScroll: true });
    }

    // ---------- Последний расчёт ----------
    // fresh — ждём результат новой задачи: если он новее показанного, решения «свежие» (баннер снимается)
    async function loadLast(fresh, focus, startedAt) {
        if (!state.result) $('roLoading').classList.remove('d-none');
        $('roLoadError').classList.add('d-none');
        try {
            const d = await api('GET', '/api/routes/optimize/last');
            const res = isObj(d.result) ? d.result : (Array.isArray(d.managers) ? d : null);
            if (!res) throw Object.assign(new Error('Сервер вернул пустой результат.'), { status: 200 });
            // результат задачи мог прийти раньше — более старым не перезаписываем
            const cur = state.result && parseTime(state.result.generated_at), got = parseTime(res.generated_at);
            if (cur && got && got < cur) return;
            const same = !!state.result && String(state.result.generated_at) === String(res.generated_at);
            if (fresh && !same) {
                acceptResult(res, focus, startedAt);
            } else {
                setResult(res);
                if (fresh) announce('Нового расчёта пока нет — на экране последний');
            }
        } catch (e) {
            if (state.result) {
                if (fresh) announce('Не удалось проверить: ' + sentence(e.message));
                return;
            }
            if (e.status === 404) {
                $('roEmpty').classList.remove('d-none');
                if (fresh) announce('Расчётов пока нет');
            } else {
                $('roLoadErrorText').textContent = 'Не удалось загрузить последний расчёт. '
                    + (e.network ? 'Нет связи с сервером — проверьте сеть и нажмите «Повторить».' : sentence(e.message));
                $('roLoadError').classList.remove('d-none');
            }
        } finally {
            $('roLoading').classList.add('d-none');
        }
    }

    // Готовый результат новой задачи: фокус — на итог. Решения до запуска в него вошли; сохранённые
    // уже во время расчёта (startedAt — начало задачи) — нет, и «пересчитайте» остаётся
    function acceptResult(res, focus, startedAt) {
        const stale = !!startedAt && state.lastDecision > startedAt;
        clearDirty();
        setResult(res);
        if (stale) markDirty();
        const n = allChanges().length;
        announce('Расчёт готов: ' + (n ? n + ' ' + plural(n, 'предложение', 'предложения', 'предложений') : 'изменений нет'));
        const a = document.activeElement;
        if (focus && (!a || a === document.body || $('roProgress').contains(a) || a === $('roRunBtn') || a === $('roRecalcBtn'))) {
            $('roResTitle').focus({ preventScroll: true });
            scrollTo(document.querySelector('.ro-resbar'));
        }
    }

    // ---------- Результат ----------
    function normalizeResult(raw) {
        const r = obj(raw);
        r.before = obj(r.before);
        r.after = obj(r.after);
        r.params = obj(r.params);
        r.customers = obj(r.customers);
        r.stale_decisions = arr(r.stale_decisions).filter(isObj);
        r.managers = arr(r.managers).filter(isObj);
        r.managers.forEach((m, i) => {
            m._i = i;
            m._color = MGR_COLORS[i] || MGR_OTHER;
            m.before = obj(m.before);
            m.after = obj(m.after);
            m.feasibility = obj(m.feasibility);
            m.days_before = arr(m.days_before).filter(d => isObj(d) && num(d.weekday) !== null);
            m.days_after = arr(m.days_after).filter(d => isObj(d) && num(d.weekday) !== null);
            m.days_before.concat(m.days_after).forEach(d => { d.stops = arr(d.stops).filter(isObj); });
            m.changes = arr(m.changes).filter(c => isObj(c) && c.customer_id !== null && c.customer_id !== undefined);
            m.changes.forEach(c => {
                c.from = obj(c.from);
                c.to = obj(c.to);
                c.effect = obj(c.effect);
                const dec = obj(c.decision);
                c.decision = { pattern: decisionOf(dec.pattern), freq: decisionOf(dec.freq) };
                if (!KINDS[c.type]) c.type = num(c.from.freq) !== num(c.to.freq) ? 'frequency' : 'move';
                c._key = m.agent_id + ':' + c.customer_id;
                c._q = searchKey(c.customer_id);
            });
            m.hints = arr(m.hints).filter(isObj);
            m.hints.forEach(x => { x._q = searchKey(x.customer_id); });
        });
        return r;

        function searchKey(id) {
            const c = obj(r.customers[String(id)]);
            return norm((c.name || '') + ' ' + (c.code || '') + ' ' + id);
        }
    }
    const decisionOf = (v) => (v === 'accepted' || v === 'rejected' ? v : null);

    const custOf = (id) => (state.result && isObj(state.result.customers[String(id)]) ? state.result.customers[String(id)] : null);
    const custName = (id) => { const c = custOf(id); return c && c.name ? String(c.name) : 'Клиент ' + id; };
    const custCode = (id) => { const c = custOf(id); return c && c.code ? String(c.code) : ''; };
    const allChanges = () => (state.result ? state.result.managers.flatMap(m => m.changes) : []);
    const kindsOf = (c) => KINDS[c.type] || KINDS.move;

    function statusOf(c) {
        const vals = kindsOf(c).map(k => c.decision[k]);
        if (vals.every(v => v === vals[0])) return vals[0] || 'none';
        return 'mixed';
    }

    function setResult(raw) {
        const prev = state.result;
        const res = normalizeResult(raw);
        state.result = res;
        state.dirty = readDirty();
        if (!prev || String(prev.generated_at) !== String(res.generated_at)) initOpen(res);
        $('roEmpty').classList.add('d-none');
        $('roLoadError').classList.add('d-none');
        $('roLoading').classList.add('d-none');
        $('roResult').classList.remove('d-none');
        renderHead();
        renderStaleResult();
        renderKpi();
        renderFuelNote();
        renderManagers();
        renderProposals();
        renderMapSection();
        renderDecisions();
        renderExport();
        renderRecalc();
        updateRunState();
        loadDecisions();       // расчёт мог отметить решения отработавшими — список и число для Excel заново
    }

    // Раскрытые группы: немного предложений — все, иначе первый менеджер с изменениями
    function initOpen(res) {
        const total = res.managers.reduce((s, m) => s + m.changes.length, 0);
        const withChanges = res.managers.filter(m => m.changes.length);
        state.open = new Set((total <= OPEN_ALL_MAX ? res.managers : withChanges.slice(0, 1)).map(m => String(m.agent_id)));
    }

    function renderHead() {
        const r = state.result, p = r.params;
        const t = parseTime(r.generated_at), snap = parseTime(r.snapshot_as_of);
        const parts = [];
        if (t) parts.push('посчитано ' + stamp(t) + (num(r.seconds) !== null ? ' за ' + duration(r.seconds) : ''));
        if (snap) parts.push('данные ERP на ' + stamp(snap));
        parts.push(p.start === 'fresh' ? 'построено с нуля' : 'улучшен текущий план');
        parts.push(p.frequencies === 'current' ? 'частота как сейчас' : 'частота по продажам');
        parts.push(r.managers.length + ' ' + plural(r.managers.length, 'менеджер', 'менеджера', 'менеджеров'));
        const capped = r.managers.filter(m => m.time_capped === true).length;
        if (capped) parts.push('у ' + capped + ' ' + plural(capped, 'менеджера', 'менеджеров', 'менеджеров')
            + ' расчёт остановлен по времени — повторный может немного отличаться');
        $('roResMeta').textContent = parts.join(' · ');
        const pill = $('roFresh');
        pill.textContent = t ? 'РАСЧЁТ ОТ ' + stamp(t) : 'ПОСЛЕДНИЙ РАСЧЁТ';
        pill.title = snap ? 'Данные ERP на ' + stamp(snap) : '';
        pill.classList.remove('d-none');
    }

    // ---------- Решения → «пересчитайте» ----------
    const resultStamp = () => (state.result ? String(state.result.generated_at || '') : '');
    function readDirty() {
        try { return !!resultStamp() && localStorage.getItem(LS_DIRTY) === resultStamp(); } catch (e) { return false; }
    }
    function markDirty() {
        state.dirty = true;
        try { if (resultStamp()) localStorage.setItem(LS_DIRTY, resultStamp()); } catch (e) { /* приватный режим */ }
        renderRecalc();
    }
    function clearDirty() {
        state.dirty = false;
        try { localStorage.removeItem(LS_DIRTY); } catch (e) { /* нет доступа к localStorage */ }
    }
    function renderRecalc() {
        $('roRecalc').classList.toggle('d-none', !state.dirty || !state.result);
    }

    // ---------- Плитки «было → стало» ----------
    // Итог компании: ключ из before/after, иначе сумма (или среднее) по менеджерам
    function total(side, key, mkey, avg) {
        const r = state.result, v = num(r[side][key]);
        if (v !== null) return v;
        if (!mkey) return null;
        const vals = r.managers.map(m => num(m[side][mkey])).filter(x => x !== null);
        if (!vals.length) return null;
        const s = vals.reduce((a, b) => a + b, 0);
        return avg ? s / vals.length : s;
    }

    // Плитка: val — узлы, sub — строки (каждая — узел, текст или их список)
    function tile(id, o) {
        const el = $(id);
        el.className = 'rt-tile' + (o.cls ? ' ' + o.cls : '');
        const v = el.querySelector('[data-v]'), s = el.querySelector('[data-s]');
        v.textContent = '';
        s.textContent = '';
        v.classList.toggle('is-empty', !!o.empty);
        v.append(...[].concat(o.val).filter(x => x !== null && x !== undefined));
        (o.sub || []).filter(Boolean).forEach(line => s.append(h('span', { class: 'ro-sub-line' }, line)));
        if (o.title) el.title = o.title; else el.removeAttribute('title');
    }

    function baNodes(was, now, d, unit) {
        return [
            h('span', { class: 'rt-sr-only', text: 'было ' }),
            h('span', { class: 'ro-was', text: fmt(was, d) }),
            h('span', { class: 'ro-arrow', 'aria-hidden': 'true', text: '→' }),
            h('span', { class: 'rt-sr-only', text: ', стало ' }),
            h('span', { class: 'ro-now', text: fmt(now, d) }),
            unit ? h('small', { text: unit }) : null,
        ];
    }

    // «на 402 меньше (−26%)» — зелёным, если стало лучше; good: +1 лучше, −1 хуже, 0 — без оценки
    function changeWords(was, now, d, unit, good) {
        const diff = now - was, t = trend(diff, d);
        if (!t) return h('b', { class: 'ro-delta', text: 'без изменений' });
        const p = was ? Math.round(diff / Math.abs(was) * 100) : 0;
        return h('b', { class: 'ro-delta' + (good > 0 ? ' is-good' : (good < 0 ? ' is-bad' : '')),
            text: 'на ' + fmt(Math.abs(diff), d) + (unit ? NB + unit : '') + (t < 0 ? ' меньше' : ' больше') + (p ? ' (' + signed(p, 0) + '%)' : '') });
    }

    // lower: true — меньше лучше, null — без оценки
    function tileBA(id, was, now, o) {
        const has = was !== null || now !== null;
        const both = was !== null && now !== null;
        const t = both ? trend(now - was, o.d) : 0;
        const good = o.lower === null ? 0 : (o.lower ? -t : t);
        tile(id, {
            cls: good > 0 ? 't-ok' : (good < 0 ? 't-danger' : ''),
            empty: !has,
            val: has ? baNodes(was, now, o.d, o.unit) : '—',
            sub: [both ? changeWords(was, now, o.d, o.word, good) : null].concat(o.sub || []),
            title: o.title,
        });
    }

    function renderKpi() {
        const r = state.result, st = state.settings || {};
        const minDay = num(st.min_day_revenue);
        const minText = minDay !== null ? fmt(minDay) + NB + 'драм' : 'норму дня';

        const vb = total('before', 'visits_week', 'visits'), va = total('after', 'visits_week', 'visits');
        // визиты «раз в 2 недели» дают половинки: 6 → 5,5, поэтому один знак после запятой
        tileBA('roKVisits', vb, va, { d: 1, lower: true,
            sub: [r.params.frequencies === 'current' ? 'частота визитов как сейчас' : 'частота — по продажам'],
            title: 'Сколько визитов менеджеры делают за неделю, по всем менеджерам расчёта.' });

        const wb = total('before', 'days_below_min', 'days_below_min'), wa = total('after', 'days_below_min', 'days_below_min');
        const days = num(r.after.days_total) ?? num(r.before.days_total);
        tileBA('roKWeak', wb, wa, { d: 1, lower: true, unit: NB + 'в нед.',
            sub: ['дней' + (days !== null ? ' из ' + fmt(days, 1) : '') + ', где шанс набрать ' + minText + ' меньше 50%'],
            title: 'Слабый день — зимой шанс набрать норму дня меньше 50%. Считается по зимним заказам, в среднем за неделю.' });

        const kb = total('before', 'manager_km_week', 'manager_km'), ka = total('after', 'manager_km_week', 'manager_km');
        const lb = num(r.before.manager_liters_week), la = num(r.after.manager_liters_week);
        tileBA('roKMgrKm', kb, ka, { d: 0, lower: true, unit: NB + 'км/нед', word: 'км',
            sub: [lb !== null || la !== null ? 'топливо ' + fmt(lb) + ' → ' + fmt(la) + NB + 'л в неделю' : null],
            title: 'Дом → клиенты дня в самом коротком порядке объезда → дом; по прямой с поправкой на извилистость дорог.' });

        // Км грузовиков есть, если заданы склад и машины менеджеров; без расхода машин нет литров, и
        // грузовики не участвуют в выборе дней (truck_costs = false) — просим указать именно расход
        const tb = total('before', 'truck_km_week', 'truck_km'), ta = total('after', 'truck_km_week', 'truck_km');
        if (tb === null && ta === null) {
            // склад уже указан — не хватает машин, закреплённых за менеджерами
            const ask = depotOf() ? h('a', { href: '/routes/settings#trucks', text: 'закрепите машины за менеджерами' })
                : h('a', { href: '/routes/settings#depot', text: 'укажите склад и машины' });
            tile('roKTruck', {
                empty: true, val: '—',
                sub: [[ask, ' — тогда программа учтёт и дизель грузовиков']],
                title: 'Без склада и машин менеджеров рейсы грузовиков не считаются и в выборе дней не участвуют.',
            });
        } else {
            const tlb = total('before', 'truck_liters_week', 'truck_liters'), tla = total('after', 'truck_liters_week', 'truck_liters');
            const noFuel = [h('a', { href: '/routes/settings#trucks', text: 'укажите расход машин' }),
                r.truck_costs === false ? ' — литры не посчитаны, грузовики не участвуют в выборе дней' : ' — литры не посчитаны'];
            tileBA('roKTruck', tb, ta, { d: 0, lower: true, unit: NB + 'км/нед', word: 'км',
                sub: [tlb !== null || tla !== null ? 'дизель ' + fmt(tlb) + ' → ' + fmt(tla) + NB + 'л в неделю' : noFuel],
                title: 'Склад → клиенты, которые заказали, → склад; в среднем по году.' });
        }

        renderRevenueTile();

        const hb = total('before', 'avg_plan_work_hours', 'avg_work_hours', true), ha = total('after', 'avg_plan_work_hours', 'avg_work_hours', true);
        const pb = total('before', 'avg_plan_hours', 'avg_plan_hours', true), pa = total('after', 'avg_plan_hours', 'avg_plan_hours', true);
        tileBA('roKHours', hb, ha, { d: 1, lower: null, unit: NB + 'ч', word: 'ч',
            sub: [pb !== null || pa !== null ? 'с дорогой из дома ' + fmt(pb, 1) + ' → ' + fmt(pa, 1) + NB + 'ч' : null],
            title: 'У клиентов — визиты и дорога между ними, в среднем за рабочий день. С дорогой из дома — весь день.' });

        renderChangesTile();
    }

    // Выручка по модели заказов «было → стало» в каждом сезоне: зима (низкий сезон), лето (пик), год.
    // Частота снижается только там, где визитов больше, чем заказов в любой сезон, — потерь быть не должно
    const SEASONS = [
        { key: 'revenue_week_low', mkey: 'revenue_low', label: 'зима' },
        { key: 'revenue_week_peak', mkey: 'revenue_peak', label: 'лето' },
        { key: 'revenue_week_year', mkey: 'revenue_year', label: 'год' },
    ];
    function renderRevenueTile() {
        const rows = SEASONS.map(s => ({ s, b: total('before', s.key, s.mkey), a: total('after', s.key, s.mkey) }))
            .filter(x => x.b !== null || x.a !== null);
        // ro-tile-wide: на телефоне плитка — во всю ширину (три строки «было → стало» не помещаются в половину)
        if (!rows.length) { tile('roKRevenue', { cls: 'ro-tile-wide', empty: true, val: '—', sub: ['выручка не посчитана'] }); return; }
        const lost = rows.filter(x => x.b !== null && x.a !== null && x.a < x.b - 1);   // 1 драм — округление
        tile('roKRevenue', {
            cls: 'ro-tile-wide ' + (lost.length ? 't-danger' : 't-ok'),
            val: h('span', { class: 'ro-rev' }, rows.map(x => h('span', { class: 'ro-revrow' },
                h('span', { class: 'l', text: x.s.label }),
                h('span', { class: 'rt-sr-only', text: ': было ' }), h('span', { class: 'ro-was', text: moneyShort(x.b) }),
                h('span', { class: 'ro-arrow', 'aria-hidden': 'true', text: '→' }),
                h('span', { class: 'rt-sr-only', text: ', стало ' }), h('span', { class: 'ro-now', text: moneyShort(x.a) })))),
            sub: [lost.length
                ? h('b', { class: 'ro-delta is-bad', text: 'меньше: ' + lost.map(x => x.s.label + ' ' + signed((x.a - x.b) / Math.abs(x.b) * 100, 1) + '%').join(', ') })
                : h('b', { class: 'ro-delta is-good', text: 'без потерь ни в один сезон' })],
            title: 'Ожидаемая выручка в неделю по модели заказов: зима — низкий сезон, лето — пик, год — в среднем. '
                + 'Частота снижается только там, где визитов больше, чем заказов в любой сезон.',
        });
    }

    function renderChangesTile() {
        const all = allChanges();
        const by = { move: 0, frequency: 0, both: 0 };
        const st = { accepted: 0, rejected: 0, mixed: 0, none: 0 };
        all.forEach(c => { by[c.type] = (by[c.type] || 0) + 1; st[statusOf(c)]++; });
        const kinds = [by.move ? 'перенос ' + fmt(by.move) : '', by.frequency ? 'частота ' + fmt(by.frequency) : '',
                       by.both ? 'оба ' + fmt(by.both) : ''].filter(Boolean).join(' · ');
        const decided = [st.accepted ? 'принято ' + fmt(st.accepted) : '', st.rejected ? 'отклонено ' + fmt(st.rejected) : '',
                         st.mixed ? 'частично ' + fmt(st.mixed) : ''].filter(Boolean).join(', ');
        tile('roKChanges', {
            cls: 't-acc',
            val: h('span', { class: 'ro-now', text: fmt(all.length) }),
            sub: all.length ? [kinds, decided || 'решений пока нет'] : ['текущий план уже близок к лучшему'],
            title: 'Перенос — другой день, частота — другое число визитов в неделю; «оба» — и то и другое.',
        });
    }

    function renderFuelNote() {
        const r = state.result, el = $('roFuelNote');
        el.textContent = '';
        if (r.fuel_price_source !== 'fallback') { el.classList.add('d-none'); return; }
        el.append(icon('fa-gas-pump'), h('span', {},
            'Цена топлива в настройках не указана — при сравнении вариантов литры посчитаны по условной цене ',
            h('b', { text: fmt(r.fuel_price_used) + NB + 'драм/л' }), '. ',
            h('a', { href: '/routes/settings#norms', text: 'Указать цены' })));
        el.classList.remove('d-none');
    }

    // ---------- 02 · Таблица менеджеров ----------
    const MCOLS = [
        { key: 'visits', d: 1, lower: true, label: 'Визитов в неделю' },
        { key: 'manager_km', d: 0, lower: true, label: 'Км менеджера' },
        { key: 'truck_km', d: 0, lower: true, label: 'Км грузовика' },
        { key: 'days_below_min', d: 1, lower: true, label: 'Слабых дней' },
        { key: 'avg_work_hours', d: 1, lower: null, label: 'Часы у клиентов' },
    ];

    function baCell(was, now, d, lower) {
        if (was === null && now === null) return h('span', { class: 'ro-muted', text: '—' });
        const t = was !== null && now !== null ? trend(now - was, d) : 0;
        const good = lower === null ? 0 : (lower ? -t : t);
        return h('span', { class: 'ro-bacell' },
            h('span', { class: 'rt-sr-only', text: 'было ' }), h('span', { class: 'was', text: fmt(was, d) }),
            h('span', { class: 'arr', 'aria-hidden': 'true', text: '→' }),
            h('span', { class: 'rt-sr-only', text: ', стало ' }),
            h('span', { class: 'now' + (good > 0 ? ' is-good' : (good < 0 ? ' is-bad' : '')), text: fmt(now, d) }));
    }

    // «зимой 517 тыс./нед → 100 000 возможно максимум в 5 из 6 дней»
    function feasNode(f) {
        const rev = num(f.revenue_week_low), max = num(f.max_days_ge_min), wd = num(f.workdays);
        if (rev === null || max === null || !wd) return h('span', { class: 'ro-muted', text: '—' });
        const minDay = num(state.settings && state.settings.min_day_revenue);
        const normText = minDay !== null ? fmt(minDay) : 'норму дня';
        const head = 'зимой ' + moneyShort(rev) + '/нед → ' + normText;
        const k = Math.max(0, Math.min(Math.floor(max), wd));
        const ok = f.reachable === true || (f.reachable !== false && k >= wd);
        let text;
        if (ok) text = head + ' возможно во все ' + wd + ' ' + plural(wd, 'день', 'дня', 'дней');
        else if (k === 0) text = head + ' не набрать ни в один из ' + wd + ' ' + genDays(wd);
        else text = head + ' возможно максимум в ' + k + ' из ' + wd + ' ' + genDays(wd);
        return h('span', { class: 'ro-feas' + (ok ? '' : ' is-warn') }, ok ? null : icon('fa-triangle-exclamation'), text,
            ok ? null : h('span', { class: 'ro-feas-note', text: normText + ' во все дни зимой недостижимо без новых клиентов' }));
    }

    function renderManagers() {
        const tb = $('roMgrBody'), r = state.result;
        tb.textContent = '';
        if (!r.managers.length) {
            tb.append(h('tr', {}, h('td', { colspan: '8', class: 'rt-empty', text: 'В расчёте нет менеджеров.' })));
            return;
        }
        r.managers.forEach(m => {
            const tags = [];
            if (m.time_capped === true) {
                tags.push(h('span', { class: 'rt-badge b-warn', text: 'стоп по времени',
                    title: 'Расчёт этого менеджера остановлен по времени — повторный расчёт может немного отличаться' }));
            }
            tb.append(h('tr', {},
                h('td', { class: 'rt-cell-name' },
                    h('div', { class: 'rt-mgr' },
                        h('span', { class: 'rt-dot', style: 'background:' + m._color, 'aria-hidden': 'true' }),
                        h('div', { class: 'rt-mgr-txt' },
                            h('a', { class: 'n ro-mgr-link', href: '#roG' + m._i, dataset: { g: String(m._i) },
                                title: 'К предложениям: ' + mgrName(m), text: mgrName(m) }),
                            m.code ? h('span', { class: 'c', text: String(m.code) }) : null,
                            tags.length ? h('span', { class: 'tags' }, tags) : null))),
                MCOLS.map(c => h('td', { class: 'w-half', dataset: { label: c.label } },
                    baCell(num(m.before[c.key]), num(m.after[c.key]), c.d, c.lower))),
                h('td', { class: 'w-half ro-cnum', dataset: { label: 'Изменений' }, text: fmt(m.changes.length) }),
                h('td', { class: 'ro-feas-cell', dataset: { label: 'Норма дня' } }, feasNode(m.feasibility))));
        });
    }

    function onMgrLink(ev) {
        const a = ev.target.closest('a[data-g]');
        if (!a) return;
        ev.preventDefault();
        const g = state.groups[+a.dataset.g];
        if (!g) return;
        if (g.sec.hidden) {   // группа скрыта фильтром — сбрасываем фильтр
            state.filter = { type: 'all', q: '' };
            $('roSearch').value = '';
            renderTypeChips();
            applyFilter();
        }
        setOpen(g, true);
        scrollTo(g.sec);
        g.toggle.focus({ preventScroll: true });
    }

    // ---------- 03 · Предложения ----------
    const patternDays = (p) => [...new Set(arr(p).map(x => num(Array.isArray(x) ? x[1] : null))
        .filter(w => w !== null && w >= 1 && w <= 7))].sort((a, b) => a - b);

    // Текст шаблона, если сервер его не прислал: «вт, каждую неделю», «чт, 1-я неделя из 2»
    function patternText(pairs, W) {
        const by = new Map();
        pairs.forEach(([w, wd]) => {
            if (!by.has(wd)) by.set(wd, new Set());
            by.get(wd).add(w);
        });
        if (!by.size) return 'нет визитов';
        const wds = [...by.keys()].sort((a, b) => a - b);
        const names = wds.map(wd => (WD_SHORT[wd] || String(wd)).toLowerCase());
        const sets = wds.map(wd => [...by.get(wd)].sort((a, b) => a - b).join(','));
        const every = Array.from({ length: W }, (_, i) => i + 1).join(',');
        if (sets.every(s => s === every)) return names.join(' и ') + ', каждую неделю';
        if (sets.every(s => s === sets[0]) && !sets[0].includes(',')) return names.join(' и ') + ', ' + sets[0] + '-я неделя из ' + W;
        return wds.map((wd, i) => names[i] + ' (нед. ' + sets[i].replace(/,/g, ' и ') + ')').join(', ');
    }
    function sideText(side) {
        if (typeof side.text === 'string' && side.text) return side.text;
        const pairs = arr(side.pattern).filter(Array.isArray).map(x => [num(x[0]) || 1, num(x[1])]).filter(x => x[1] !== null);
        return patternText(pairs, Math.max(2, ...pairs.map(x => x[0])));
    }

    function renderProposals() {
        const box = $('roProps');
        box.textContent = '';
        state.entries = new Map();
        state.groups = [];
        const r = state.result;
        if (!r.managers.length) box.append(h('p', { class: 'rt-empty', text: 'В расчёте нет менеджеров.' }));
        r.managers.forEach(m => box.append(groupNode(m)));
        renderTypeChips();
        applyFilter();
    }

    function groupNode(m) {
        const gid = 'roG' + m._i;
        const open = state.open.has(String(m.agent_id));
        const g = { m, days: [], hints: [], shown: 0, busy: false, empty: null, hintsBox: null };
        g.toggle = h('button', { type: 'button', class: 'ro-group-toggle', 'aria-expanded': String(open), 'aria-controls': gid + 'b' },
            icon('fa-chevron-down chev'),
            h('span', { class: 'rt-dot', style: 'background:' + m._color, 'aria-hidden': 'true' }),
            h('span', { class: 'n', text: mgrName(m) }),
            m.code ? h('span', { class: 'c', text: String(m.code) }) : null);
        g.toggle.addEventListener('click', () => setOpen(g, g.toggle.getAttribute('aria-expanded') !== 'true'));
        g.stats = h('span', { class: 'ro-gstats' });
        g.all = h('button', { type: 'button', class: 'rt-btn rt-btn-sm ro-btn-ok' }, icon('fa-check-double'), h('span'));
        g.all.addEventListener('click', () => acceptAll(g));
        const mapBtn = h('button', { type: 'button', class: 'rt-hintbtn', 'aria-label': 'Показать на карте: ' + mgrName(m) },
            icon('fa-map-location-dot'), 'на карте');
        mapBtn.addEventListener('click', () => showOnMap(m));
        g.body = h('div', { class: 'ro-group-body', id: gid + 'b', hidden: !open });

        // Внутри менеджера — по дню «стало» (дни недели шаблона после изменения)
        const byDay = new Map();
        m.changes.forEach(c => {
            const wds = patternDays(c.to.pattern);
            const key = wds.join(',') || '?';
            if (!byDay.has(key)) byDay.set(key, { wds, list: [] });
            byDay.get(key).list.push(c);
        });
        [...byDay.values()]
            .sort((a, b) => (a.wds[0] || 9) - (b.wds[0] || 9) || a.wds.length - b.wds.length || a.wds.join().localeCompare(b.wds.join()))
            .forEach(dg => {
                dg.list.sort((a, b) => custName(a.customer_id).localeCompare(custName(b.customer_id), 'ru'));
                const day = { count: h('span', { class: 's' }), entries: [] };
                const ul = h('ul', { class: 'ro-rows' });
                dg.list.forEach(c => {
                    const e = { c, m, g, el: null, side: null, saving: false, error: '' };
                    buildRow(e);
                    state.entries.set(c._key, e);
                    day.entries.push(e);
                    ul.append(e.el);
                });
                const title = dg.wds.length ? cap(dg.wds.map(w => WD_FULL[w]).join(' + ')) : 'День не указан';
                day.el = h('div', { class: 'ro-dayg' },
                    h('h4', { class: 'ro-dayg-head' },
                        dg.wds.map(w => h('span', { class: 'sw', style: 'background:' + (WD_COLORS[w] || MGR_OTHER), 'aria-hidden': 'true' })),
                        title, day.count),
                    ul);
                g.days.push(day);
                g.body.append(day.el);
            });
        if (!m.changes.length) {
            g.empty = h('p', { class: 'ro-empty-row', text: 'Изменений нет — дни и частоту этого менеджера программа менять не предлагает.' });
            g.body.append(g.empty);
        }
        // Подсказки: не применяются, только к сведению
        if (m.hints.length) {
            const ul = h('ul');
            m.hints.forEach(x => {
                const code = custCode(x.customer_id);
                const li = h('li', {}, icon(HINT_ICON[x.kind] || 'fa-lightbulb'),
                    h('span', {}, h('span', { class: 'n', text: custName(x.customer_id) }), code ? h('span', { class: 'c', text: code }) : null,
                        ' — ', String(x.text || '')));
                g.hints.push({ el: li, q: x._q });
                ul.append(li);
            });
            g.hintsBox = h('div', { class: 'ro-hints' }, h('h4', { text: 'Подсказки — программа их не применяет' }), ul);
            g.body.append(g.hintsBox);
        }
        g.sec = h('section', { class: 'ro-group', id: gid, 'aria-label': 'Предложения — ' + mgrName(m) },
            h('div', { class: 'ro-group-head' },
                h('h3', { class: 'ro-group-title' }, g.toggle),
                g.stats,
                h('div', { class: 'ro-group-acts' }, mapBtn, m.changes.length ? g.all : null)),
            g.body);
        state.groups.push(g);
        return g.sec;
    }

    function setOpen(g, open) {
        g.toggle.setAttribute('aria-expanded', String(open));
        g.body.hidden = !open;
        if (open) state.open.add(String(g.m.agent_id));
        else state.open.delete(String(g.m.agent_id));
    }

    function buildRow(e) {
        const c = e.c, id = c.customer_id, cust = custOf(id) || {};
        const meta = [custCode(id), cust.abc ? 'класс ' + cust.abc : ''].filter(Boolean).join(' · ');
        const effects = EFFECTS.map(f => {
            const v = num(c.effect[f.key]);
            if (v === null) return null;
            const t = trend(v, f.d);
            return h('span', { class: 'ro-eff' + (t < 0 ? ' is-good' : (t > 0 ? ' is-bad' : '')),
                title: f.title + ' — ' + (t < 0 ? 'лучше' : (t > 0 ? 'хуже' : 'без изменений')) },
                f.label, h('b', { text: signed(v, f.d) + (t ? f.unit : '') }));
        }).filter(Boolean);
        const main = h('div', { class: 'ro-row-main' },
            h('div', { class: 'ro-cust' }, h('span', { class: 'n', text: custName(id) }), meta ? h('span', { class: 'c', text: meta }) : null),
            h('div', { class: 'ro-change' },
                h('span', { class: 'rt-badge', text: TYPE_TEXT[c.type] }),
                h('span', { class: 'rt-sr-only', text: 'было: ' }),
                h('span', { class: 'from', text: sideText(c.from) }),
                h('span', { class: 'arr', 'aria-hidden': 'true', text: '→' }),
                h('span', { class: 'rt-sr-only', text: ', стало: ' }),
                h('span', { class: 'to', text: sideText(c.to) })),
            c.reason ? h('div', { class: 'ro-reason', text: String(c.reason) }) : null,
            effects.length ? h('div', { class: 'ro-effects' }, effects) : null);
        e.side = h('div', { class: 'ro-row-side' });
        e.el = h('li', { class: 'ro-row', dataset: { key: c._key } }, main, e.side);
        refreshRow(e);
    }

    // Статус и кнопки строки. Фокус был на кнопке строки — переходит на главную кнопку нового состояния
    function refreshRow(e) {
        const c = e.c, st = statusOf(c), S = STATUS[st];
        e.el.className = 'ro-row' + (st === 'none' ? '' : ' is-' + st);
        e.el.setAttribute('aria-busy', e.saving ? 'true' : 'false');
        const hadFocus = e.side.contains(document.activeElement);
        e.side.textContent = '';
        e.side.append(h('span', { class: 'rt-badge ro-status ' + S.cls }, e.saving ? spin() : icon(S.icon), e.saving ? 'сохраняю…' : S.text));
        const name = custName(c.customer_id);
        const btn = (cls, ico, text, act) => h('button', { type: 'button', class: 'rt-btn rt-btn-sm ' + cls, dataset: { act },
            'aria-disabled': e.saving ? 'true' : null, 'aria-label': text + ': ' + name }, icon(ico), text);
        const acts = h('div', { class: 'ro-actions' });
        if (st === 'none' || st === 'mixed') acts.append(btn('ro-btn-ok', 'fa-check', 'Принять', 'accept'));
        if (st === 'none') acts.append(btn('rt-btn-ghost', 'fa-xmark', 'Отклонить', 'reject'));
        if (st !== 'none') acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Отменить решение', 'reset'));
        e.side.append(acts);
        if (e.error) e.side.append(h('div', { class: 'ro-row-err', role: 'alert', text: e.error }));
        if (hadFocus) {
            const b = acts.querySelector(st === 'none' ? '[data-act="accept"]' : '[data-act="reset"]') || acts.querySelector('button');
            if (b) b.focus({ preventScroll: true });
        }
    }

    const passType = (c) => state.filter.type === 'all' || (state.filter.type === 'move' ? c.type !== 'frequency' : c.type !== 'move');
    const passQuery = (q) => !state.filter.q || q.includes(state.filter.q);
    const filtering = () => state.filter.type !== 'all' || !!state.filter.q;

    function applyFilter() {
        const f = state.filter, on = filtering();
        let shown = 0, all = 0;
        state.groups.forEach(g => {
            let gShown = 0;
            g.days.forEach(d => {
                let n = 0;
                d.entries.forEach(e => {
                    const ok = passType(e.c) && passQuery(e.c._q);
                    e.el.hidden = !ok;
                    if (ok) n++;
                });
                d.el.hidden = !n;
                d.count.textContent = '· ' + n + ' ' + plural(n, 'клиент', 'клиента', 'клиентов');
                gShown += n;
            });
            let hShown = 0;
            g.hints.forEach(x => {
                const ok = f.type === 'all' && passQuery(x.q);
                x.el.hidden = !ok;
                if (ok) hShown++;
            });
            if (g.hintsBox) g.hintsBox.hidden = !hShown;
            if (g.empty) g.empty.hidden = on;
            g.shown = gShown;
            g.sec.hidden = on && !gShown && !hShown;
            shown += gShown;
            all += g.m.changes.length;
            renderGroupHead(g);
        });
        $('roShown').textContent = all ? (on ? 'показано ' + fmt(shown) + ' из ' + fmt(all) : 'всего ' + fmt(all)) : '';
        $('roNoMatch').hidden = !(on && state.groups.length && state.groups.every(g => g.sec.hidden));
    }

    function renderGroupHead(g) {
        const n = g.m.changes.length;
        const st = { accepted: 0, rejected: 0, mixed: 0, none: 0 };
        g.m.changes.forEach(c => { st[statusOf(c)]++; });
        let pending = 0;
        g.days.forEach(d => d.entries.forEach(e => { if (!e.el.hidden && !e.saving && statusOf(e.c) === 'none') pending++; }));
        const s = g.stats;
        s.textContent = '';
        if (!n) {
            s.append('изменений нет');
        } else {
            if (filtering()) s.append('показано ', h('b', { text: fmt(g.shown) }), ' из ');
            s.append(h('b', { text: fmt(n) }), ' ' + plural(n, 'предложение', 'предложения', 'предложений'));
            if (st.accepted) s.append(' · принято ', h('b', { text: fmt(st.accepted) }));
            if (st.rejected) s.append(' · отклонено ', h('b', { text: fmt(st.rejected) }));
            if (st.mixed) s.append(' · частично ', h('b', { text: fmt(st.mixed) }));
            if (st.none && st.none !== n) s.append(' · без решения ', h('b', { text: fmt(st.none) }));
        }
        const word = filtering() ? 'Принять показанные' : 'Принять все';
        g.all.querySelector('span').textContent = g.busy ? 'Сохраняю…' : word + ' (' + pending + ')';
        g.all.setAttribute('aria-disabled', g.busy || !pending ? 'true' : 'false');
        g.all.setAttribute('aria-label', word + ' предложения без решения у менеджера ' + mgrName(g.m) + ': ' + pending);
        g.all.title = pending ? '' : 'Нет предложений без решения';
    }

    function renderTypeChips() {
        const all = allChanges();
        const cnt = { all: all.length, move: all.filter(c => c.type !== 'frequency').length, frequency: all.filter(c => c.type !== 'move').length };
        $('roTypeChips').querySelectorAll('.rt-chip').forEach(b => {
            b.setAttribute('aria-pressed', b.dataset.type === state.filter.type ? 'true' : 'false');
            b.querySelector('.ro-cnt').textContent = fmt(cnt[b.dataset.type]);
        });
    }

    function onTypeChip(ev) {
        const b = ev.target.closest('.rt-chip');
        if (!b || !b.dataset.type || b.dataset.type === state.filter.type) return;
        state.filter.type = b.dataset.type;
        renderTypeChips();
        applyFilter();
    }

    function onSearch() {
        const q = norm($('roSearch').value.trim());
        if (q === state.filter.q) return;
        state.filter.q = q;
        // поиск раскрывает менеджеров, у которых нашлись клиенты
        if (q) state.groups.forEach(g => {
            if (g.days.some(d => d.entries.some(e => e.c._q.includes(q))) || g.hints.some(x => x.q.includes(q))) setOpen(g, true);
        });
        applyFilter();
    }

    function onRowAction(ev) {
        const b = ev.target.closest('button[data-act]');
        if (!b || b.getAttribute('aria-disabled') === 'true') return;
        const li = b.closest('.ro-row');
        const e = li && state.entries.get(li.dataset.key);
        if (e) decide([e], b.dataset.act);
    }

    // ---------- Решения: оптимистично, одним запросом, с откатом при ошибке ----------
    const whyText = (err) => (err.network ? 'нет связи с сервером' : (err.status === 401 ? 'нужно войти заново'
        : String(err.message || 'ошибка сервера').replace(/[.!]\s*$/, '')));

    // Тело решения: «было» (from) — строка «было» предложения: решение привязано к плану, на котором
    // принималось, и если план клиента в ERP потом изменится, оно не применится молча
    function decisionItem(e, kind, action) {
        const side = kind === 'pattern' ? 'pattern' : 'freq';
        return { customer_id: e.c.customer_id, agent_id: e.m.agent_id, kind, value: e.c.to[side], from: e.c.from[side], action };
    }

    // Все выбранные строки (и оба вида у «перенос и частота») — одним запросом, одной транзакцией:
    // сохранилось всё или ничего, поэтому при ошибке откатываются все строки
    async function decide(list, action) {
        if (!(action in ACTION_STATUS)) return;
        const target = ACTION_STATUS[action];
        const todo = [];
        list.forEach(e => {
            if (e.saving) return;
            // отправляем только те виды решения, что меняются (у «частично» — недостающий)
            const kinds = kindsOf(e.c).filter(k => e.c.decision[k] !== target);
            if (!kinds.length) return;
            e.prev = Object.assign({}, e.c.decision);
            e.kinds = kinds;
            kinds.forEach(k => { e.c.decision[k] = target; });
            e.saving = true;
            e.error = '';
            refreshRow(e);
            todo.push(e);
        });
        if (!todo.length) return;
        afterDecisions(todo);
        const owners = [];
        const items = [];
        todo.forEach(e => e.kinds.forEach(k => { items.push(decisionItem(e, k, action)); owners.push(e); }));
        let err = null;
        try {
            await api('POST', '/api/routes/decisions', items.length === 1 ? items[0] : { items });
        } catch (x) {
            err = x;
        }
        // ошибки пачки — по строкам: items.<№>.<поле>
        const rowErr = new Map();
        if (err && err.data && isObj(err.data.errors)) {
            Object.entries(err.data.errors).forEach(([k, v]) => {
                const m = /^items\.(\d+)/.exec(k);
                if (m && owners[+m[1]] && !rowErr.has(owners[+m[1]])) rowErr.set(owners[+m[1]], String(v));
            });
        }
        todo.forEach(e => {
            if (err) {
                e.kinds.forEach(k => { e.c.decision[k] = decisionOf(e.prev[k]); });
                const own = rowErr.get(e);
                e.error = own ? 'Не сохранилось: ' + own.replace(/[.!]\s*$/, '') + '.'
                    : (rowErr.size ? 'Не сохранилось вместе с другими — в списке есть ошибка. Нажмите ещё раз.'
                        : 'Не сохранилось: ' + whyText(err) + '. Нажмите ещё раз.');
            }
            e.saving = false;
            refreshRow(e);
        });
        if (!err) {
            state.lastDecision = Date.now();
            markDirty();
        }
        afterDecisions(todo);
        loadDecisions();
        const done = { accept: 'Принято', reject: 'Отклонено', reset: 'Решение отменено' }[action];
        const who = todo.length === 1 ? ': ' + custName(todo[0].c.customer_id) : ' ' + fmt(todo.length);
        announce(err ? 'Не сохранилось' + who + '. ' + sentence(cap(whyText(err))) : done + who);
    }

    function afterDecisions(list) {
        new Set(list.map(e => e.g)).forEach(renderGroupHead);
        renderChangesTile();
        renderExport();
        renderRecalc();
    }

    // ---------- 05 · Ваши решения ----------
    // Список действующих решений и число изменений для Excel — с сервера (решения могли прийти из другой
    // вкладки, по менеджерам вне этого расчёта, отработать в ERP или устареть)
    let decTimer = null;
    function loadDecisions() {
        clearTimeout(decTimer);
        decTimer = setTimeout(fetchDecisions, 250);   // серия быстрых решений — один запрос списка
    }

    async function fetchDecisions() {
        const seq = ++state.decSeq;
        try {
            const d = await api('GET', '/api/routes/decisions');
            if (seq !== state.decSeq) return;
            state.decisions = { data_as_of: d.data_as_of, summary: obj(d.summary), decisions: arr(d.decisions).filter(isObj) };
            state.decError = '';
        } catch (e) {
            if (seq !== state.decSeq) return;
            state.decError = e.network ? 'нет связи с сервером' : whyText(e);
        }
        renderDecisions();
        renderExport();
    }

    const decName = (x) => (x.customer_name ? String(x.customer_name) : 'Клиент ' + x.customer_id);
    const dataMismatch = (asOf) => {
        const a = parseTime(asOf), b = state.result ? parseTime(state.result.snapshot_as_of) : null;
        return a && b && a.getTime() !== b.getTime() ? { now: a, calc: b } : null;
    };

    function renderDecisions() {
        const d = state.decisions, counts = $('roDecCounts'), list = $('roDecList'), warn = $('roDecWarn');
        const toggle = $('roDecToggle'), reset = $('roDecReset');
        counts.textContent = '';
        list.textContent = '';
        warn.textContent = '';
        const errBox = $('roDecErr');
        errBox.classList.toggle('d-none', !state.decError);
        errBox.textContent = state.decError ? 'Список решений не загрузился: ' + state.decError + '.' : '';
        if (!d) {
            counts.append(state.decError ? '—' : 'Загружаю решения…');
            toggle.hidden = true;
            reset.setAttribute('aria-disabled', 'true');
            warn.classList.add('d-none');
            return;
        }
        const s = d.summary, items = d.decisions;
        const acc = num(s.accepted) || 0, rej = num(s.rejected) || 0, stale = num(s.stale) || 0, exp = num(s.export_changes);
        if (!items.length) {
            counts.append('Решений нет — примите или отклоните предложения выше.');
        } else {
            counts.append('принято ', h('b', { text: fmt(acc) }), ' · отклонено ', h('b', { text: fmt(rej) }));
            if (stale) counts.append(' · ', h('b', { class: 'is-warn', text: fmt(stale) }), ' ' + plural(stale, 'устарело', 'устарели', 'устарели'));
            if (exp !== null) counts.append(' · в план для ERP ' + plural(exp, 'войдёт', 'войдут', 'войдут') + ' ', h('b', { text: fmt(exp) }),
                ' ' + plural(exp, 'изменение', 'изменения', 'изменений'));
        }
        toggle.hidden = !items.length;
        toggle.setAttribute('aria-expanded', String(state.decOpen && !!items.length));
        toggle.textContent = state.decOpen ? 'скрыть список' : 'показать список (' + fmt(items.length) + ')';
        list.hidden = !state.decOpen || !items.length;
        reset.setAttribute('aria-disabled', !items.length || state.decBusy ? 'true' : 'false');
        items.forEach((x, i) => list.append(decRow(x, i)));

        const notes = [];
        if (stale) {
            notes.push(h('span', {}, h('b', { text: fmt(stale) + ' ' + plural(stale, 'решение устарело', 'решения устарели', 'решений устарели') }),
                ' — план клиента в ERP изменился после решения, поэтому ' + plural(stale, 'оно не применяется', 'они не применяются', 'они не применяются')
                + ' ни в расчёте, ни в плане для ERP. Отмените ' + plural(stale, 'его', 'их', 'их') + ' или пересчитайте.'));
        }
        const mm = dataMismatch(d.data_as_of);
        if (mm) notes.push(h('span', {}, 'Данные ERP обновились после расчёта: расчёт — на ' + stamp(mm.calc) + ', сейчас — на '
            + stamp(mm.now) + '. Пересчитайте, чтобы предложения совпали с текущим планом.'));
        warn.classList.toggle('d-none', !notes.length);
        if (notes.length) warn.append(icon('fa-triangle-exclamation'), h('span', { class: 'rt-alert-text' }, notes.map(n => h('span', { class: 'ro-sub-line' }, n))));
    }

    function decRow(x, i) {
        const st = x.stale ? { cls: 'b-warn', icon: 'fa-triangle-exclamation', text: 'устарело' }
            : (x.status === 'accepted' ? STATUS.accepted : STATUS.rejected);
        const name = decName(x);
        return h('li', { class: 'ro-row ro-dec-row' + (x.stale ? ' is-stale' : (x.status === 'rejected' ? ' is-rejected' : ' is-accepted')) },
            h('div', { class: 'ro-row-main' },
                h('div', { class: 'ro-cust' }, h('span', { class: 'n', text: name }),
                    h('span', { class: 'c', text: [x.customer_code, x.agent_code || x.agent_name].filter(Boolean).map(String).join(' · ') })),
                h('div', { class: 'ro-change' },
                    h('span', { class: 'rt-badge', text: x.kind === 'freq' ? 'частота' : 'дни' }),
                    x.from_text ? [h('span', { class: 'rt-sr-only', text: 'было: ' }), h('span', { class: 'from', text: String(x.from_text) }),
                        h('span', { class: 'arr', 'aria-hidden': 'true', text: '→' }), h('span', { class: 'rt-sr-only', text: ', стало: ' })] : null,
                    h('span', { class: 'to', text: String(x.to_text || '—') })),
                x.stale ? h('div', { class: 'ro-reason is-warn', text: 'сейчас в ERP: ' + String(x.current_text || '—') + ' — решение не применяется' }) : null,
                x.manager_included === false ? h('div', { class: 'ro-reason', text: 'менеджер не в расчёте — в план для ERP не войдёт' }) : null),
            h('div', { class: 'ro-row-side' },
                h('span', { class: 'rt-badge ro-status ' + st.cls }, icon(st.icon), st.text),
                h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { i: String(i) },
                    'aria-disabled': state.decBusy ? 'true' : null, 'aria-label': 'Отменить решение: ' + name },
                icon('fa-rotate-left'), 'Отменить')));
    }

    // Решение сняли не из строки предложения (список, «Сбросить все») — статусы строк на экране вслед за сервером
    function clearRowDecisions(match) {
        const touched = [];
        state.entries.forEach(e => {
            let changed = false;
            kindsOf(e.c).forEach(k => {
                if (e.c.decision[k] && match(e, k)) { e.c.decision[k] = null; changed = true; }
            });
            if (changed) { refreshRow(e); touched.push(e); }
        });
        if (touched.length) afterDecisions(touched);
    }

    const sameValue = (kind, a, b) => (kind === 'pattern' ? patternKey(a) === patternKey(b) : num(a) === num(b));

    async function resetOne(x, i) {
        if (state.decBusy) return;
        const hadFocus = $('roDecList').contains(document.activeElement);
        state.decBusy = true;
        renderDecisions();
        try {
            await api('POST', '/api/routes/decisions', { customer_id: x.customer_id, agent_id: x.agent_id, kind: x.kind, value: x.value, action: 'reset' });
            clearRowDecisions((e, k) => k === x.kind && String(e.m.agent_id) === String(x.agent_id)
                && String(e.c.customer_id) === String(x.customer_id) && sameValue(k, e.c.to[k === 'pattern' ? 'pattern' : 'freq'], x.value));
            state.lastDecision = Date.now();
            markDirty();
            announce('Решение отменено: ' + decName(x));
        } catch (e) {
            announce('Не удалось отменить решение: ' + sentence(whyText(e)));
            $('roDecErr').textContent = 'Не удалось отменить решение по «' + decName(x) + '»: ' + whyText(e) + '.';
            $('roDecErr').classList.remove('d-none');
        } finally {
            state.decBusy = false;
            await fetchDecisions();
            if (hadFocus) {   // строка ушла из списка — фокус на соседнюю, иначе на кнопку списка
                const btns = $('roDecList').querySelectorAll('button[data-i]');
                const next = btns[Math.min(i, btns.length - 1)];
                (next && !$('roDecList').hidden ? next : $('roDecToggle').hidden ? $('roDecReset') : $('roDecToggle')).focus();
            }
        }
    }

    async function resetAll() {
        const n = state.decisions ? state.decisions.decisions.length : 0;
        if (state.decBusy || !n) return;
        if (!window.confirm('Сбросить все решения (' + n + ')?\n\nПринятые и отклонённые предложения снова станут «без решения», '
            + 'в план для ERP не войдёт ни одно изменение. Отменить сброс нельзя.')) return;
        state.decBusy = true;
        renderDecisions();
        try {
            const d = await api('POST', '/api/routes/decisions', { action: 'reset_all' });
            clearRowDecisions(() => true);
            state.lastDecision = Date.now();
            markDirty();
            announce('Все решения сброшены' + (num(d.deleted) !== null ? ': ' + fmt(d.deleted) : ''));
        } catch (e) {
            announce('Не удалось сбросить решения: ' + sentence(whyText(e)));
            $('roDecErr').textContent = 'Не удалось сбросить решения: ' + whyText(e) + '.';
            $('roDecErr').classList.remove('d-none');
        } finally {
            state.decBusy = false;
            fetchDecisions();
        }
    }

    function onDecList(ev) {
        const b = ev.target.closest('button[data-i]');
        if (!b || b.getAttribute('aria-disabled') === 'true' || !state.decisions) return;
        const x = state.decisions.decisions[+b.dataset.i];
        if (x) resetOne(x, +b.dataset.i);
    }

    // Итог расчёта: принятые решения, которые в нём не применены (план клиента в ERP изменился)
    function renderStaleResult() {
        const box = $('roStaleDec'), list = state.result.stale_decisions;
        box.textContent = '';
        box.classList.toggle('d-none', !list.length);
        if (!list.length) return;
        const n = list.length;
        const ul = h('ul', {}, list.slice(0, STALE_LIST_MAX).map(x => h('li', {},
            h('b', { text: decName(x) }), x.agent_code ? ' (' + x.agent_code + ')' : '', ': решено ',
            (x.from_text ? '«' + x.from_text + '» → ' : '') + '«' + String(x.to_text || '—') + '», сейчас в ERP — «' + String(x.current_text || '—') + '»')));
        if (n > STALE_LIST_MAX) ul.append(h('li', { text: 'и ещё ' + fmt(n - STALE_LIST_MAX) + ' — весь список в разделе «Ваши решения»' }));
        box.append(icon('fa-triangle-exclamation'), h('span', { class: 'rt-alert-text' },
            h('b', { text: fmt(n) + ' ' + plural(n, 'принятое решение не применено', 'принятых решения не применены', 'принятых решений не применены') }),
            ' в этом расчёте — план клиента в ERP изменился после решения.', ul));
    }

    async function acceptAll(g) {
        if (g.busy) return;
        const list = [];
        g.days.forEach(d => d.entries.forEach(e => { if (!e.el.hidden && !e.saving && statusOf(e.c) === 'none') list.push(e); }));
        if (!list.length) { announce('У этого менеджера нет предложений без решения'); return; }
        g.busy = true;
        g.all.querySelector('i').className = 'rt-spin-inline';
        renderGroupHead(g);
        try {
            await decide(list, 'accept');
        } finally {
            g.busy = false;
            g.all.querySelector('i').className = 'fas fa-check-double';
            renderGroupHead(g);
        }
    }

    // ---------- 04 · Карта ----------
    const weekOf = (d) => Math.max(1, num(d.week) || 1);
    const dayKey = (d) => weekOf(d) + '-' + num(d.weekday);
    const wdColor = (wd) => WD_COLORS[wd] || MGR_OTHER;
    const stopGeo = (st) => !!st && st.coord_source !== 'none' && num(st.lat) !== null && num(st.lon) !== null;

    function curMgr() {
        const r = state.result;
        if (!r) return null;
        return r.managers.find(m => String(m.agent_id) === state.mapAgent) || null;
    }
    const modeDays = (m) => (m ? (state.mapMode === 'before' ? m.days_before : m.days_after) : []);
    // Длина цикла на карте: «было» — как в плане ERP, «стало» — цикл расчёта (2 недели)
    function cycleOf(m) {
        const w = modeDays(m).reduce((a, d) => Math.max(a, weekOf(d)), 1);
        return state.mapMode === 'after' ? Math.max(w, num(state.result.cycle_weeks) || 1) : w;
    }
    const selectedDay = (m) => (state.mapDay ? modeDays(m).find(d => dayKey(d) === state.mapDay) || null : null);

    function homeOf(m) {
        const own = obj(m.home);
        if (num(own.lat) !== null && num(own.lon) !== null) return [num(own.lat), num(own.lon)];
        return state.homes.get(String(m.agent_id)) || null;
    }
    function depotOf() {
        const dp = state.result && isObj(state.result.depot) ? state.result.depot : null;
        if (dp && num(dp.lat) !== null && num(dp.lon) !== null) return [num(dp.lat), num(dp.lon)];
        return state.depot;
    }

    function ensureMap() {
        if (state.map || state.mapFailed) return;
        const el = $('roMap');
        if (typeof window.L === 'undefined') {
            state.mapFailed = true;
            el.classList.add('rt-map-fallback');
            el.textContent = 'Карта не загрузилась (нет доступа к cdn.jsdelivr.net). Предложения и Excel работают.';
            return;
        }
        const map = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
        map.setView(YEREVAN, 9);
        // колесо мыши масштабирует карту только после клика по ней — страница прокручивается свободно
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.layers = { points: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map), base: L.layerGroup().addTo(map) };
    }

    function renderMapSection() {
        const r = state.result, sel = $('roMapMgr');
        sel.textContent = '';
        r.managers.forEach(m => sel.add(new Option(mgrName(m) + (m.code ? ' · ' + m.code : ''), String(m.agent_id))));
        if (!curMgr()) {
            const m = r.managers.find(x => x.changes.length) || r.managers[0];
            state.mapAgent = m ? String(m.agent_id) : null;
            state.mapDay = null;
        }
        sel.value = state.mapAgent || '';
        sel.disabled = !r.managers.length;
        document.querySelectorAll('input[name="roMapMode"]').forEach(x => { x.checked = x.value === state.mapMode; });
        ensureMap();
        renderDays();
        drawMap();
    }

    function renderDays() {
        const box = $('roDays'), m = curMgr();
        box.textContent = '';
        const days = modeDays(m);
        if (state.mapDay && !days.some(d => dayKey(d) === state.mapDay)) state.mapDay = null;
        if (!days.length) {
            box.append(h('p', { class: 'ro-muted mb-0', text: m ? 'В этом плане у менеджера нет дней с визитами.' : 'Нет менеджеров.' }));
            renderDayInfo();
            return;
        }
        const W = cycleOf(m);
        for (let w = 1; w <= W; w++) {
            const list = days.filter(d => weekOf(d) === w).sort((a, b) => num(a.weekday) - num(b.weekday));
            if (!list.length) continue;
            const grid = h('div', { class: 'ro-daylist', role: 'group', 'aria-label': W > 1 ? 'Неделя ' + w : 'Дни недели' },
                list.map(d => dayButton(d, W)));
            box.append(W > 1 ? h('div', { class: 'ro-weekrow' }, h('span', { class: 'lbl', 'aria-hidden': 'true', text: 'Неделя ' + w }), grid) : grid);
        }
        renderDayInfo();
    }

    function dayButton(d, W) {
        const wd = num(d.weekday), w = weekOf(d), visits = num(d.visits) || 0;
        const full = (WD_FULL[wd] || 'день ' + wd) + (W > 1 ? ', неделя ' + w : '');
        return h('button', { type: 'button', class: 'ro-dayb' + (w > 1 ? ' is-w2' : ''), style: '--wd:' + wdColor(wd),
            'aria-pressed': String(state.mapDay === dayKey(d)), dataset: { key: dayKey(d) },
            'aria-label': cap(full) + ': ' + fmt(visits) + ' ' + plural(visits, 'визит', 'визита', 'визитов') + ', ' + fmt(d.manager_km, 1) + ' км' },
            h('span', { class: 'd', text: WD_SHORT[wd] || String(wd) }),
            h('span', { class: 's', text: fmt(visits) + ' виз · ' + fmt(d.manager_km, 0) + ' км' }));
    }

    function selectDay(key) {
        state.mapDay = key && state.mapDay !== key ? key : null;
        $('roDays').querySelectorAll('.ro-dayb').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.key === state.mapDay)));
        renderDayInfo();
        drawMap();
    }

    function renderDayInfo() {
        const box = $('roDayInfo'), m = curMgr();
        box.textContent = '';
        const d = m && selectedDay(m);
        if (!d) return;
        const W = cycleOf(m), wd = num(d.weekday), visits = num(d.visits) || 0;
        const minDay = num(state.settings && state.settings.min_day_revenue);
        const noGeo = d.stops.filter(st => !stopGeo(st)).length;
        box.append(...[
            h('div', { class: 'ro-dayinfo-t', text: cap(WD_FULL[wd] || 'день ' + wd) + (W > 1 ? ', неделя ' + weekOf(d) : '')
                + (state.mapMode === 'before' ? ' — было' : ' — стало') }),
            h('div', { text: fmt(visits) + ' ' + plural(visits, 'визит', 'визита', 'визитов') + ' · ' + fmt(d.manager_km, 1) + NB + 'км · весь день ' + hm(d.plan_minutes) }),
            num(d.p_day_ge_min) !== null ? h('div', { text: 'шанс набрать ' + (minDay !== null ? fmt(minDay) + NB + 'драм' : 'норму дня') + ' зимой — ' + pct(d.p_day_ge_min) }) : null,
            noGeo ? h('div', { class: 'ro-warnline' }, icon('fa-location-crosshairs'),
                fmt(noGeo) + ' ' + plural(noGeo, 'клиент', 'клиента', 'клиентов') + ' без координат — на карте их нет') : null,
            homeOf(m) ? null : h('div', { class: 'ro-warnline' }, icon('fa-house'), 'дом неизвестен — линия от первого клиента до последнего'),
            h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm mt-2', dataset: { all: '1' } }, icon('fa-xmark'), 'Показать все дни'),
        ].filter(Boolean));   // append(null) вставил бы текст «null»
    }

    function updateMapNote(m) {
        const note = $('roMapNote');
        if (!m) { note.textContent = ''; return; }
        const ids = new Set(), noGeo = new Set();
        modeDays(m).forEach(d => d.stops.forEach(st => { (stopGeo(st) ? ids : noGeo).add(String(st.customer_id)); }));
        noGeo.forEach(id => { if (ids.has(id)) noGeo.delete(id); });
        note.textContent = 'на карте ' + fmt(ids.size) + ' ' + plural(ids.size, 'клиент', 'клиента', 'клиентов')
            + (noGeo.size ? ' · без координат ' + fmt(noGeo.size) : '');
    }

    function renderMapLegend(m) {
        const box = $('roMapLegend');
        box.textContent = '';
        if (!m) return;
        const W = cycleOf(m);
        const wds = [...new Set(modeDays(m).map(d => num(d.weekday)))].sort((a, b) => a - b);
        const item = (sw, text) => h('span', { class: 'rt-lg rt-lg-static' }, sw, text);
        wds.forEach(wd => box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:' + wdColor(wd), 'aria-hidden': 'true' }),
            WD_SHORT[wd] || String(wd))));
        if (W > 1) {
            box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:#a7b0c0', 'aria-hidden': 'true' }), 'неделя 1 или каждую неделю'));
            box.append(item(h('span', { class: 'ro-lg-sw is-hollow', style: 'color:#a7b0c0', 'aria-hidden': 'true' }), 'только неделя 2 — пунктир'));
        }
        box.append(item(h('span', { class: 'rt-lg-ico', style: 'border:1.5px solid #a7b0c0;border-radius:50%', 'aria-hidden': 'true' }, icon('fa-house')), 'Дом менеджера'));
        if (depotOf()) box.append(item(h('span', { class: 'rt-lg-ico', style: 'background:#eef1f6;color:#0c0f14', 'aria-hidden': 'true' }, icon('fa-warehouse')), 'Склад'));
    }

    // Дни клиента в плане режима: «пн и чт, каждую неделю»
    function visitsText(m, side, cid) {
        const days = side === 'before' ? m.days_before : m.days_after;
        const pairs = [];
        days.forEach(d => { if (d.stops.some(st => String(st.customer_id) === String(cid))) pairs.push([weekOf(d), num(d.weekday)]); });
        const W = Math.max(side === 'after' ? num(state.result.cycle_weeks) || 1 : 1, ...days.map(weekOf));
        return patternText(pairs, W);
    }

    function tipNode(m, cid) {
        return h('div', {}, h('b', { text: custName(cid) }), h('br'),
            h('span', { style: 'color:#a7b0c0', text: visitsText(m, state.mapMode, cid) }));
    }

    function popupNode(m, cid) {
        const c = custOf(cid) || {};
        const ch = m.changes.find(x => String(x.customer_id) === String(cid)) || null;
        const row = (k, v) => h('div', { class: 'rt-pop-row' }, h('span', { text: k }), h('b', { text: v }));
        const lam = num(c.lam_year);
        return h('div', { class: 'rt-pop' },
            h('div', { class: 'rt-pop-t', text: custName(cid) }),
            h('div', { class: 'rt-pop-s', text: [c.code, c.abc ? 'класс ' + c.abc : ''].filter(Boolean).map(String).join(' · ') || '—' }),
            row('Было', ch ? sideText(ch.from) : visitsText(m, 'before', cid)),
            row('Стало', ch ? sideText(ch.to) : visitsText(m, 'after', cid)),
            ch && ch.reason ? h('div', { class: 'rt-pop-note', text: String(ch.reason) }) : null,
            lam !== null ? row('Заказывает', fmt(lam, 2) + ' ' + (Number.isInteger(round(lam, 2)) ? plural(lam, 'раз', 'раза', 'раз') : 'раза') + ' в неделю') : null,
            row('Решение', ch ? STATUS[statusOf(ch)].text : 'изменений нет'));
    }

    function pin(cls, color, ico, size) {
        return L.divIcon({
            className: 'rt-pin ' + cls,
            html: h('span', { style: color ? 'color:' + color : null }, icon(ico)),
            iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        });
    }

    function drawMap() {
        const m = curMgr();
        updateMapNote(m);
        renderMapLegend(m);
        if (!state.map) return;
        const layers = state.layers;
        layers.points.clearLayers();
        layers.route.clearLayers();
        layers.base.clearLayers();
        const bounds = [];
        if (m) {
            const days = modeDays(m), W = cycleOf(m), sel = selectedDay(m);
            // Точка — клиент × день недели; только неделя 2 — пунктиром и полупрозрачно
            const pts = new Map();
            days.forEach(d => {
                const wd = num(d.weekday), w = weekOf(d);
                d.stops.forEach(st => {
                    if (!stopGeo(st)) return;
                    const k = st.customer_id + '|' + wd;
                    let p = pts.get(k);
                    if (!p) pts.set(k, p = { cid: st.customer_id, wd, weeks: new Set(), ll: [num(st.lat), num(st.lon)] });
                    p.weeks.add(w);
                });
            });
            const onRoute = sel ? new Set(sel.stops.map(st => String(st.customer_id))) : null;
            pts.forEach(p => {
                if (sel && p.wd === num(sel.weekday) && onRoute.has(String(p.cid))) return;   // эти — номерами маршрута
                const color = wdColor(p.wd), hollow = W > 1 && !p.weeks.has(1);
                L.circleMarker(p.ll, {
                    radius: 5.5, weight: hollow ? 2 : 1.5, color: hollow ? color : '#0c0f14', dashArray: hollow ? '3 3' : null,
                    fillColor: color, fillOpacity: hollow ? 0.15 : (sel ? 0.28 : 0.92), opacity: sel ? 0.45 : 0.95,
                })
                    .bindTooltip(() => tipNode(m, p.cid), { direction: 'top', offset: [0, -6] })
                    .bindPopup(() => popupNode(m, p.cid), { maxWidth: 320 })
                    .addTo(layers.points);
                if (!sel) bounds.push(p.ll);
            });
            const home = homeOf(m);
            if (sel) drawRoute(m, sel, home, bounds);
            if (home) {
                L.marker(home, { icon: pin('rt-pin-home', m._color, 'fa-house', 24), title: 'Дом: ' + mgrName(m), zIndexOffset: 500 })
                    .bindTooltip(() => h('div', {}, h('b', { text: 'Дом' }), ' · ' + mgrName(m)), { direction: 'top', offset: [0, -10] })
                    .addTo(layers.base);
                bounds.push(home);
            }
        }
        const dp = depotOf();
        if (dp) {
            L.marker(dp, { icon: pin('rt-pin-depot', null, 'fa-warehouse', 28), title: 'Склад', zIndexOffset: 1000 })
                .bindTooltip(() => h('div', {}, h('b', { text: 'Склад' }), ' — отсюда выезжают машины'), { direction: 'top', offset: [0, -12] })
                .addTo(layers.base);
        }
        state.map.invalidateSize();
        if (bounds.length) state.map.fitBounds(L.latLngBounds(bounds).pad(0.08), { animate: !RM, maxZoom: 15 });
        else state.map.setView(YEREVAN, 9, { animate: !RM });
    }

    // Маршрут дня: дом → остановки в порядке объезда → дом; визиты без координат пропускаются
    function drawRoute(m, d, home, bounds) {
        const color = wdColor(num(d.weekday)), w2 = weekOf(d) > 1;
        const stops = d.stops.map((st, i) => ({ st, n: i + 1 })).filter(x => stopGeo(x.st));
        const path = stops.map(x => [num(x.st.lat), num(x.st.lon)]);
        if (home) { path.unshift(home); path.push(home); }
        if (path.length >= 2) {
            L.polyline(path, { color: '#0c0f14', weight: 7, opacity: 0.6, interactive: false }).addTo(state.layers.route);
            L.polyline(path, { color, weight: 3, opacity: 0.95, lineJoin: 'round', dashArray: w2 ? '8 6' : null, interactive: false })
                .addTo(state.layers.route);
        }
        stops.forEach(x => {
            const ll = [num(x.st.lat), num(x.st.lon)];
            const ic = L.divIcon({ className: 'rt-stop', html: h('span', { style: 'color:' + color, text: String(x.n) }),
                iconSize: [22, 22], iconAnchor: [11, 11] });
            L.marker(ll, { icon: ic, keyboard: false, riseOnHover: true })
                .bindTooltip(() => h('div', {}, h('b', { text: x.n + '. ' }), custName(x.st.customer_id)), { direction: 'top', offset: [0, -10] })
                .bindPopup(() => popupNode(m, x.st.customer_id), { maxWidth: 320 })
                .addTo(state.layers.route);
            bounds.push(ll);
        });
        if (home) bounds.push(home);
    }

    function showOnMap(m) {
        state.mapAgent = String(m.agent_id);
        state.mapDay = null;
        $('roMapMgr').value = state.mapAgent;
        renderDays();
        drawMap();
        scrollTo($('roMapSection'));
        announce('На карте: ' + mgrName(m));
    }

    // ---------- 06 · План для ERP (Excel) ----------
    const acceptedChanges = () => (state.result ? state.result.managers.flatMap(m => m.changes
        .filter(c => kindsOf(c).some(k => c.decision[k] === 'accepted')).map(c => ({ c, m }))) : []);

    // Сколько изменений войдёт в файл — считает сервер (как plan-export): принятые и у менеджеров вне
    // этого расчёта, без устаревших и уже исполненных в ERP
    function renderExport() {
        const s = state.decisions ? state.decisions.summary : null;
        const n = s ? num(s.export_changes) : null, stale = s ? num(s.stale) || 0 : 0;
        const btn = $('roExportBtn'), note = $('roExportNote');
        btn.setAttribute('aria-disabled', n === 0 || state.exporting ? 'true' : 'false');
        let text;
        if (n === null) {
            text = state.decError ? 'Не удалось узнать, сколько изменений войдёт в файл, — его всё равно можно скачать.'
                : 'Считаю принятые изменения…';
        } else if (n) {
            text = 'В файл ' + plural(n, 'войдёт', 'войдут', 'войдут') + ' ' + fmt(n) + ' '
                + plural(n, 'принятое изменение', 'принятых изменения', 'принятых изменений') + '.';
        } else {
            text = 'Принятых изменений нет — план совпадает с текущим. Примите предложения выше, и они войдут в файл.';
        }
        if (stale) text += ' Устаревших решений — ' + fmt(stale) + ': в файл они не войдут.';
        note.textContent = text;
        note.classList.toggle('is-off', n === 0);
    }

    function setExportBusy(on) {
        state.exporting = on;
        const btn = $('roExportBtn');
        btn.setAttribute('aria-busy', on ? 'true' : 'false');
        btn.querySelector('i').className = on ? 'rt-spin-inline' : 'fas fa-file-excel';
        btn.querySelector('span').textContent = on ? 'Готовлю файл…' : 'Скачать план для ERP (Excel)';
        renderExport();
    }

    const pick = (o, ...keys) => {
        for (const k of keys) if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k];
        return null;
    };
    const str = (v) => (v === null || v === undefined ? '' : String(v));
    function mgrOf(agentId) {
        const k = String(agentId);
        return (state.result.managers.find(m => String(m.agent_id) === k)
            || (state.managers || []).find(m => String(m.agent_id) === k)) || null;
    }
    function markText(v) {
        if (Array.isArray(v)) return v.map(markText).filter(Boolean).join(', ');
        if (v === null || v === undefined || v === '') return '';
        const MARK = { move: 'перенос', frequency: 'частота', freq: 'частота', both: 'перенос, частота' };
        return MARK[v] || String(v);
    }

    // Лист «План»: менеджер (код, имя), неделя цикла, день, №, код клиента, клиент, отметка;
    // если сервер прислал адрес доставки ERP — ещё колонка с ним (для ввода в ERP, ТЗ §6)
    function planSheet(d) {
        const src = ([d.rows, d.plan, d.lines].find(Array.isArray) || []).filter(isObj);
        const addr = src.some(x => pick(x, 'address_id') !== null);
        const rows = src.map(x => {
            const m = mgrOf(pick(x, 'agent_id', 'manager_id')) || {};
            const cid = pick(x, 'customer_id');
            const wd = num(pick(x, 'weekday'));
            const r = [
                str(pick(x, 'agent_code', 'manager_code') ?? m.code),
                str(pick(x, 'agent_name', 'manager_name') ?? m.name),
                num(pick(x, 'week', 'cycle_week')),
                wd !== null ? (WD_SHORT[wd] || String(wd)) : str(pick(x, 'day', 'day_label', 'label')),
                num(pick(x, 'order', 'rownum', 'no', 'seq', 'position')),
                str(pick(x, 'customer_code') ?? (cid !== null ? custCode(cid) : '')),
                str(pick(x, 'customer_name') ?? (cid !== null ? custName(cid) : '')),
                markText(pick(x, 'mark', 'change', 'change_type')),
            ];
            if (addr) r.push(pick(x, 'address_id'));
            return r;
        });
        return { rows, head: addr ? PLAN_HEAD.concat('Адрес доставки (ID)') : PLAN_HEAD, cols: addr ? PLAN_COLS.concat(18) : PLAN_COLS };
    }

    const patternKey = (p) => JSON.stringify(arr(p).filter(Array.isArray).map(x => [num(x[0]), num(x[1])])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1]));

    // Лист «Изменения»: принятые строки; с сервера, иначе — из результата на экране
    function changeRows(d) {
        const src = Array.isArray(d.changes) ? d.changes.filter(isObj)
            : acceptedChanges().map(({ c, m }) => Object.assign({ agent_id: m.agent_id }, c));
        return src.map(x => {
            const aid = pick(x, 'agent_id'), cid = pick(x, 'customer_id');
            const m = mgrOf(aid) || {};
            const own = m.changes ? m.changes.find(c => String(c.customer_id) === String(cid)) : null;
            const c = own || {};
            const side = (s, flat) => (isObj(x[s]) ? sideText(x[s]) : str(pick(x, flat, s))) || (isObj(c[s]) ? sideText(c[s]) : '');
            // эффект предложения верен, только если в план ушёл тот же шаблон (при принятой одной частоте он другой)
            const same = !isObj(x.to) || !own || patternKey(obj(x.to).pattern) === patternKey(c.to.pattern);
            const eff = isObj(x.effect) ? x.effect : (same ? obj(c.effect) : {});
            const type = pick(x, 'type', 'change_type') || c.type;
            return [
                str(pick(x, 'agent_code', 'manager_code') ?? m.code),
                str(pick(x, 'agent_name', 'manager_name') ?? m.name),
                str(pick(x, 'customer_code') ?? (cid !== null ? custCode(cid) : '')),
                str(pick(x, 'customer_name') ?? (cid !== null ? custName(cid) : '')),
                TYPE_TEXT[type] || markText(type),
                side('from', 'from_text'),
                side('to', 'to_text'),
                str(pick(x, 'reason') ?? c.reason),
                num(eff.manager_km_week), num(eff.truck_km_week), num(eff.weak_days_week), num(eff.minutes_week),
            ];
        });
    }

    function sheet(head, rows, widths) {
        const ws = XLSX.utils.aoa_to_sheet([head].concat(rows));
        ws['!cols'] = widths.map(w => ({ wch: w }));
        if (rows.length) ws['!autofilter'] = { ref: XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: rows.length, c: head.length - 1 } }) };
        return ws;
    }

    async function exportPlan() {
        const btn = $('roExportBtn');
        if (state.exporting) return;
        if (btn.getAttribute('aria-disabled') === 'true') { announce($('roExportNote').textContent); return; }
        $('roExportErr').classList.add('d-none');
        $('roExportWarn').classList.add('d-none');
        if (typeof window.XLSX === 'undefined') {
            showExportError('Библиотека Excel не загрузилась (нет доступа к cdn.jsdelivr.net). Обновите страницу.');
            return;
        }
        setExportBusy(true);
        try {
            const d = await api('GET', '/api/routes/plan-export');
            renderExportWarn(d);
            const plan = planSheet(d), changes = changeRows(d);
            if (!plan.rows.length) throw new Error('Сервер вернул пустой план.');
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, sheet(plan.head, plan.rows, plan.cols), 'План');
            XLSX.utils.book_append_sheet(wb, sheet(CHANGE_HEAD, changes, CHANGE_COLS), 'Изменения');
            const file = 'plan_marshrutov_' + ymd(new Date()) + '.xlsx';
            XLSX.writeFile(wb, file);
            announce('Файл ' + file + ' скачан: строк плана ' + plan.rows.length + ', изменений ' + changes.length);
        } catch (e) {
            showExportError('Файл не получился: ' + (e.network ? 'нет связи с сервером. Проверьте сеть и нажмите ещё раз.' : sentence(e.message)));
        } finally {
            setExportBusy(false);
        }
    }

    function showExportError(text) {
        $('roExportErrText').textContent = text;
        $('roExportErr').classList.remove('d-none');
    }

    // Файл собран, но стоит знать: устаревшие решения в него не вошли; план собран по другим данным ERP,
    // чем расчёт на экране
    function renderExportWarn(d) {
        const box = $('roExportWarn');
        box.textContent = '';
        const lines = [];
        const stale = arr(d.stale_decisions).filter(isObj);
        if (stale.length) {
            const ul = h('ul', {}, stale.slice(0, STALE_LIST_MAX).map(x => h('li', {}, h('b', { text: decName(x) }),
                x.agent_code ? ' (' + x.agent_code + ')' : '', ': решено «' + String(x.to_text || '—') + '», сейчас в ERP — «'
                + String(x.current_text || '—') + '»')));
            if (stale.length > STALE_LIST_MAX) ul.append(h('li', { text: 'и ещё ' + fmt(stale.length - STALE_LIST_MAX) }));
            lines.push(h('span', {}, h('b', { text: 'Не вошли ' + fmt(stale.length) + ' ' + plural(stale.length, 'устаревшее решение', 'устаревших решения', 'устаревших решений') }),
                ' — план клиента в ERP изменился после решения.', ul));
        }
        const mm = dataMismatch(d.data_as_of);
        if (mm) lines.push(h('span', {}, 'План для ERP собран по данным ERP на ' + stamp(mm.now) + ', а расчёт на экране — на '
            + stamp(mm.calc) + '. Принятые изменения применены к текущему плану ERP; чтобы предложения совпали с ним, пересчитайте.'));
        box.classList.toggle('d-none', !lines.length);
        if (lines.length) box.append(icon('fa-triangle-exclamation'), h('span', { class: 'rt-alert-text' }, lines.map(x => h('span', { class: 'ro-sub-line' }, x))));
    }

    // ---------- Справочники: менеджеры, дома, склад, порог дня ----------
    async function loadRefs() {
        $('roMgrsErr').classList.add('d-none');
        const [ov, st] = await Promise.allSettled([api('GET', '/api/routes/overview'), api('GET', '/api/routes/settings')]);
        if (st.status === 'fulfilled' && isObj(st.value.settings)) {
            state.settings = st.value.settings;
            if (state.result) {
                renderKpi();
                renderManagers();
                renderDayInfo();
            }
        }
        if (ov.status === 'fulfilled') {
            const d = ov.value;
            const list = arr(d.managers).filter(m => isObj(m) && m.agent_id !== null && m.agent_id !== undefined);
            state.managers = list.map(m => ({ agent_id: m.agent_id, code: m.code, name: m.name, included: m.included !== false }));
            state.homes = new Map();
            list.forEach(m => {
                const hh = obj(m.home);
                if (num(hh.lat) !== null && num(hh.lon) !== null) state.homes.set(String(m.agent_id), [num(hh.lat), num(hh.lon)]);
            });
            const dp = obj(d.depot);
            state.depot = num(dp.lat) !== null && num(dp.lon) !== null ? [num(dp.lat), num(dp.lon)] : null;
            state.pick = new Set(state.managers.filter(m => m.included).map(m => String(m.agent_id)));
            renderPicker();
            if (state.result) {
                renderKpi();       // склад известен — плитка грузовиков просит именно то, чего не хватает
                drawMap();
            }
        } else {
            const e = ov.reason || {};
            const box = $('roMgrs');
            box.textContent = '';
            box.append(h('span', { class: 'ro-muted', text: 'Список менеджеров не загрузился — расчёт пойдёт по менеджерам «в расчёте» из настроек.' }));
            const err = $('roMgrsErr');
            err.textContent = '';
            const again = h('button', { type: 'button', class: 'rt-hintbtn', text: 'Загрузить снова' });
            again.addEventListener('click', () => {
                box.textContent = '';
                box.append(h('span', { class: 'ro-muted' }, spin(), ' Загружаю список менеджеров…'));
                loadRefs();
            });
            err.append(h('span', { text: e.network ? 'Нет связи с сервером.' : sentence(e.message || 'Ошибка сервера.') }), again);
            err.classList.remove('d-none');
            updateRunState();
        }
    }

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        syncNavOffset();
        window.addEventListener('resize', syncNavOffset);
        renderHints();
        $('roMgrs').parentElement.querySelector('.ro-quick').hidden = true;
        updateRunState();

        $('roForm').addEventListener('submit', (e) => {
            e.preventDefault();
            if ($('roRunBtn').getAttribute('aria-disabled') === 'true' && !running()) {
                announce('Выберите хотя бы одного менеджера');
                return;
            }
            run(readParams());
        });
        $('roForm').addEventListener('change', (e) => { if (e.target.name === 'roStart' || e.target.name === 'roFreq') renderHints(); });
        $('roMgrs').addEventListener('change', onPick);
        $('roMgrAll').addEventListener('click', () => setPick('all'));
        $('roMgrNone').addEventListener('click', () => setPick('none'));
        $('roRunRetry').addEventListener('click', () => {
            hideRunError();
            if (state.retry) state.retry();
        });
        $('roNetRetry').addEventListener('click', () => { if (state.job) poll(); });
        $('roLoadRetry').addEventListener('click', () => loadLast(false));
        $('roRecalcBtn').addEventListener('click', () => {
            if ($('roRecalcBtn').getAttribute('aria-disabled') === 'true') { announce('Расчёт уже идёт — дождитесь окончания'); return; }
            recalc();
        });

        $('roTypeChips').addEventListener('click', onTypeChip);
        $('roSearch').addEventListener('input', debounce(onSearch, 150));
        $('roProps').addEventListener('click', onRowAction);
        $('roMgrBody').addEventListener('click', onMgrLink);

        $('roMapMgr').addEventListener('change', (e) => {
            state.mapAgent = e.target.value;
            state.mapDay = null;
            renderDays();
            drawMap();
        });
        document.querySelectorAll('input[name="roMapMode"]').forEach(x => x.addEventListener('change', (e) => {
            state.mapMode = e.target.value === 'before' ? 'before' : 'after';
            renderDays();
            drawMap();
        }));
        $('roDays').addEventListener('click', (e) => {
            const b = e.target.closest('.ro-dayb');
            if (b) selectDay(b.dataset.key);
        });
        $('roDayInfo').addEventListener('click', (e) => {
            if (!e.target.closest('button[data-all]')) return;
            const key = state.mapDay;
            selectDay(null);
            const b = key && $('roDays').querySelector('.ro-dayb[data-key="' + key + '"]');
            if (b) b.focus();
        });
        $('roExportBtn').addEventListener('click', exportPlan);
        $('roDecList').addEventListener('click', onDecList);
        $('roDecReset').addEventListener('click', () => {
            if ($('roDecReset').getAttribute('aria-disabled') === 'true') { announce('Решений нет — сбрасывать нечего'); return; }
            resetAll();
        });
        $('roDecToggle').addEventListener('click', () => {
            state.decOpen = !state.decOpen;
            renderDecisions();
        });

        loadRefs();
        loadLast(false);
        resumeJob();
    });
})();

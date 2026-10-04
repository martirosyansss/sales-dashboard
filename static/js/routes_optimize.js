/* «Маршруты · оптимизация» /routes/optimize — этап 3, режим А: магазины остаются у своих менеджеров,
   программа предлагает дни визитов и частоту; этап 4, режим Б (params.mode = 'transfer', «Дополнительно»):
   ещё и передать магазин другому менеджеру — группа «Передать другому менеджеру» (по одному, с
   подтверждением), баланс «отдаёт / получает» в карточке, стрелки передач на карте (docs/plans/stage-4-plan.md). Интерфейс «для чайников» (docs/plans/ux-simple-plan.md):
   шаг 1 — посчитать; шаг 2 — главное человеческим языком, три показателя, карточки менеджеров,
   предложения менеджера группами по смыслу, карта (свёрнута); шаг 3 — принять и выгрузить Excel.
   Контракт — docs/plans/stage-3-plan.md §10 и §10.1 (не меняется):
     POST /api/routes/optimize → job_id (409 — расчёт уже идёт, в ответе job_id идущей задачи);
     опрос GET /api/routes/optimize/<job_id> раз в 1,5 с; GET /api/routes/optimize/last (404 — расчётов
     ещё не было); POST /api/routes/decisions — одно решение или {items: [...]} одной транзакцией,
     {action: 'reset_all'}; GET /api/routes/decisions — «Ваши решения» и число изменений для Excel;
     GET /api/routes/plan-export — строки плана, Excel собирается здесь (SheetJS, глобальный XLSX).
   Галочки менеджеров, дома, склад и сезоны — из GET /api/routes/overview, порог дня и рабочие дни — из
   GET /api/routes/settings. Тексты «было → станет», причины, группы и итоги считаются здесь из этих ответов.
   Безопасность: строки из ERP и из ответов сервера выводятся только текстом — узлы собирает h()
   через textContent; в подсказки и попапы Leaflet уходят готовые DOM-узлы, а не HTML-строки. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const NB = ' ';     // неразрывный пробел: число не отрывается от единицы
    const MINUS = '−';

    const WD_SHORT = { 1: 'Երկ', 2: 'Երք', 3: 'Չրք', 4: 'Հնգ', 5: 'Ուրբ', 6: 'Շբթ', 7: 'Կիր' };
    const WD_LOWER = { 1: 'երկ', 2: 'երք', 3: 'չրք', 4: 'հնգ', 5: 'ուրբ', 6: 'շբթ', 7: 'կիր' };
    const WD_FULL = { 1: 'երկուշաբթի', 2: 'երեքշաբթի', 3: 'չորեքշաբթի', 4: 'հինգշաբթի', 5: 'ուրբաթ', 6: 'շաբաթ', 7: 'կիրակի' };
    const WD_FROM = { 1: 'երկուշաբթիից', 2: 'երեքշաբթիից', 3: 'չորեքշաբթիից', 4: 'հինգշաբթիից', 5: 'ուրբաթից', 6: 'շաբաթ օրվանից', 7: 'կիրակիից' };
    const MONTHS_FULL = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    // Цвет точки на карте — день недели. Оттенки из палитры менеджеров обзора (контраст к тёмной карте ≥ 3:1)
    const WD_COLORS = { 1: '#18c1fc', 2: '#fe904d', 3: '#b0a2ff', 4: '#b8b90c', 5: '#14cfa3', 6: '#fe80c0', 7: '#8b93a7' };
    // Цвета менеджеров — как в обзоре: слот по позиции менеджера в ответе
    const MGR_COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3',
                        '#0d9298', '#b8b90c', '#ae55c1', '#37981b', '#fe80c0', '#d64651'];
    const MGR_OTHER = '#8b93a7';
    // Вид изменения — для листа «Изменения» в Excel (как было)
    const TYPE_TEXT = { move: 'տեղափոխում', frequency: 'հաճախականություն', both: 'տեղափոխում և հաճախականություն', remove: 'հանել', transfer: 'փոխանցել' };
    // Какие решения отправляет изменение: у «both» — два, шаблон дней и частота; «убрать из маршрута» — remove;
    // «передать другому менеджеру» (этап 4) — transfer: кому и в какие дни
    const KINDS = { move: ['pattern'], frequency: ['freq'], both: ['pattern', 'freq'], remove: ['remove'], transfer: ['transfer'] };
    const ACTION_STATUS = { accept: 'accepted', reject: 'rejected', reset: null };
    // Состояние строки предложения — подпись и значок (без решения — только кнопки)
    const ROW_STATUS = {
        accepted: { cls: 'is-ok', icon: 'fa-check', text: 'Ընդունված է' },
        rejected: { cls: 'is-off', icon: 'fa-minus', text: 'Թողնված է ինչպես կա' },
        mixed: { cls: 'is-warn', icon: 'fa-circle-half-stroke', text: 'Մասամբ ընդունված' },
    };
    // «Убрать из маршрута» (§15): принять — убрать, отклонить — оставить
    const REMOVE_ROW_STATUS = {
        accepted: { cls: 'is-bad', icon: 'fa-user-minus', text: 'Կհանվի երթուղուց' },
        rejected: { cls: 'is-ok', icon: 'fa-user-check', text: 'Մնում է երթուղում' },
    };
    // «Передать другому менеджеру»: принять — передать, отклонить — оставить у своего
    const TRANSFER_ROW_STATUS = {
        accepted: { cls: 'is-ok', icon: 'fa-people-arrows', text: 'Կփոխանցվի' },
        rejected: { cls: 'is-off', icon: 'fa-minus', text: 'Մնում է իր մենեջերի մոտ' },
    };
    const DECISION_WORD = { none: 'դեռ որոշված չէ', accepted: 'ընդունված է', rejected: 'թողնված է ինչպես կա', mixed: 'մասամբ ընդունված' };
    const REMOVE_WORD = { none: 'դեռ որոշված չէ', accepted: 'հանել երթուղուց', rejected: 'թողնել երթուղում', mixed: 'դեռ որոշված չէ' };
    const TRANSFER_WORD = { none: 'դեռ որոշված չէ', accepted: 'փոխանցել', rejected: 'թողնել իր մենեջերի մոտ', mixed: 'դեռ որոշված չէ' };
    // Затих, потерян, ни одного заказа за год (§15)
    const SILENT = new Set(['dormant', 'lost', 'never']);
    // Группы предложений по смыслу (бриф): порядок, значок, короткая подпись переключателя;
    // incoming — магазины, которые этому менеджеру передают другие (решение то же, что у отдающего)
    const GROUP_ORDER = ['transfer', 'incoming', 'freq', 'weekly', 'move', 'offday', 'winback', 'remove', 'other'];
    const GROUP_ICON = { transfer: 'fa-people-arrows', incoming: 'fa-people-arrows', freq: 'fa-calendar-minus', weekly: 'fa-calendar-plus', move: 'fa-shuffle',
                         offday: 'fa-calendar-xmark', winback: 'fa-hand-holding-heart', remove: 'fa-user-minus', other: 'fa-pen', hints: 'fa-lightbulb' };
    // Решения только по одному магазину, с подтверждением: «Принять все в группе» у этих групп нет
    const ONE_BY_ONE = new Set(['remove', 'transfer', 'incoming']);
    const RUN_NOTE_TAIL = 'ERP-ից միայն կարդում ենք';
    const PLAN_HEAD = ['Մենեջերի կոդ', 'Մենեջեր', 'Ցիկլի շաբաթ', 'Օր', '№', 'Հաճախորդի կոդ', 'Հաճախորդ', 'Նշում'];
    const PLAN_COLS = [14, 28, 13, 7, 6, 14, 42, 18];
    const CHANGE_HEAD = ['Մենեջերի կոդ', 'Մենեջեր', 'Հաճախորդի կոդ', 'Հաճախորդ', 'Ինչ է փոխվում', 'Էր', 'Կլինի', 'Պատճառ',
                         'Մենեջերի կմ շաբաթում', 'Բեռնատարի կմ շաբաթում', 'Թույլ օրեր շաբաթում', 'Րոպե շաբաթում'];
    const CHANGE_COLS = [14, 28, 14, 42, 18, 26, 26, 46, 12, 12, 12, 10];
    const POLL_MS = 1500;
    const POLL_MAX_MS = 15000;
    const JOB_TTL_MS = 3 * 3600 * 1000;      // сохранённую задачу старше 3 часов не подхватываем
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
    function moneyShort(v) {
        const n = num(v);
        if (n === null) return '—';
        const a = Math.abs(n);
        if (a >= 1e6) return fmt(n / 1e6, 1) + NB + 'մլն';
        if (a >= 1e3) return fmt(Math.round(n / 1e3)) + NB + 'հազ.';
        return fmt(Math.round(n));
    }
    // «≈ 22 000» — сумма округлена так, чтобы было понятно порядок, а не копейки
    function moneyRound(v) {
        const a = Math.abs(v), step = a >= 10000 ? 1000 : (a >= 1000 ? 100 : 10);
        return fmt(Math.round(v / step) * step);
    }
    // Порядковое числительное: «1-ին», «2-րդ» (глоссарий 1.9); после числа существительное — в ед. ч.
    const ord = (n) => n + (n === 1 ? '-ին' : '-րդ');
    function hm(minutes) {
        const n = num(minutes);
        if (n === null) return '—';
        const m = Math.max(0, Math.round(n)), hh = Math.floor(m / 60), r = m % 60;
        return hh ? (r ? hh + NB + 'ժ ' + r + NB + 'րոպե' : hh + NB + 'ժ') : r + NB + 'րոպե';
    }
    function duration(sec) {
        const n = num(sec);
        if (n === null) return '';
        const s = Math.max(0, Math.round(n)), m = Math.floor(s / 60), r = s % 60;
        return m ? m + NB + 'րոպե' + (r ? ' ' + r + NB + 'վրկ' : '') : r + NB + 'վրկ';
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
    // «30.09, ժամը 23:21»
    const dayTime = (t) => t.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ', ժամը '
        + t.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    const ymd = (t) => t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
    // Месяцы подряд — «январь – март» (в т.ч. через Новый год), иначе перечислением
    function monthsText(list) {
        const ms = [...new Set(arr(list).map(Number).filter(m => m >= 1 && m <= 12))].sort((a, b) => a - b);
        if (!ms.length) return '';
        if (ms.length === 1) return MONTHS_FULL[ms[0] - 1];
        const gaps = ms.filter((m, i) => ms[(i + 1) % ms.length] !== (m % 12) + 1);
        if (gaps.length === 1) {
            const end = gaps[0], start = ms[(ms.indexOf(end) + 1) % ms.length];
            return MONTHS_FULL[start - 1] + ' – ' + MONTHS_FULL[end - 1];
        }
        return ms.map(m => MONTHS_FULL[m - 1]).join(', ');
    }

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
    // Число, выделенное маркером: зелёным — лучше, красным — хуже
    const mark = (text, tone) => h('b', { class: 'rt-mark' + (tone > 0 ? ' is-good' : (tone < 0 ? ' is-bad' : '')), text });

    // ---------- Подсказки «?» (toggletip) ----------
    // Кнопка «?» раскрывает пояснение рядом: клик, Enter или пробел — показать или скрыть; Esc и клик мимо — скрыть.
    // Пузырь стоит в DOM сразу за кнопкой (скринридер читает его следующим), на экране — у кнопки, в пределах окна.
    let tipSeq = 0;
    function tip(text, label) {
        const id = 'roTip' + (++tipSeq);
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
        if (top + ht > vh - 8 && r.top - ht - 8 > 8) top = r.top - ht - 8;   // снизу не влезает — над кнопкой
        bubble.style.left = Math.round(left) + 'px';
        bubble.style.top = Math.round(top) + 'px';
    }
    const openTips = () => [...document.querySelectorAll('#rtOptimize .rt-tip-btn[aria-expanded="true"]')];
    function initTips() {
        const root = $('rtOptimize');
        root.addEventListener('click', (e) => {
            const b = e.target.closest('.rt-tip-btn');
            if (b) {
                e.preventDefault();
                const open = b.getAttribute('aria-expanded') !== 'true';
                openTips().forEach(x => { if (x !== b) setTip(x, false); });
                setTip(b, open);
            }
        });
        document.addEventListener('click', (e) => {
            if (e.target.closest('.rt-tip')) return;
            openTips().forEach(x => setTip(x, false));
        });
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

    // ---------- Состояние ----------
    const state = {
        managers: null,        // из обзора: [{agent_id, code, name, included}] — галочки параметров
        homes: new Map(),      // agent_id → [lat, lon] — дома из обзора
        depot: null,           // [lat, lon] — склад из обзора
        season: null,          // сезоны из обзора: месяцы зимы — для подсказок
        roads: false,          // км по дорогам на карте (обзор: distance_source = 'roads')
        settings: null,        // настройки: порог дня, рабочие дни, «потерян» через N дней
        pick: new Set(),       // выбранные менеджеры (agent_id строкой)
        result: null,          // результат расчёта (§10.1)
        byAgent: new Map(),    // agent_id строкой → менеджер результата
        panelAgent: null,      // чьи предложения открыты (agent_id строкой)
        pf: { group: 'all', q: '', raw: '' },   // фильтр предложений в панели: группа и поиск
        pgroups: [],           // группы панели: {key, list, el, listEl, toggle, …}
        rows: new Map(),       // «агент:клиент» → {el, side} — строки, которые сейчас на экране
        paramsOpen: false,     // после расчёта развернули «Изменить параметры»
        dirty: false,          // после расчёта менялись решения — пора пересчитать
        lastDecision: 0,       // когда на этой странице последний раз сохранилось решение (мс)
        job: null,             // {id, timer, inflight, fails, started, resumed, alive}
        starting: false,
        tick: null,
        lastRun: null,         // параметры последнего запуска — для «Повторить»
        retry: null,
        map: null, mapFailed: false, layers: null,
        mapAgent: null, mapMode: 'after', mapDay: null,
        mapTransfers: false,   // стрелки передач на карте («Показать передачи» или «На карте» у передачи)
        mapFocus: null,        // передача, у которой открыть подсказку после отрисовки (c._key)
        transferMarkers: null, // c._key → маркер магазина передачи на карте
        exporting: false,
        decisions: null,       // GET /api/routes/decisions: {data_as_of, summary, decisions}
        decError: '',          // список решений не загрузился
        decSeq: 0,             // номер запроса списка: ответ старого запроса не перезаписывает новый
        decBusy: false,        // идёт «Сбросить все» или отмена решения из списка
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
        400: 'Սերվերը չընդունեց հարցումը։', 401: 'Անհրաժեշտ է մուտք գործել համակարգ։',
        403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։', 404: 'Չի գտնվել։',
        409: 'Հաշվարկն արդեն ընթանում է։', 415: 'Սերվերը չընդունեց հարցումը։',
        500: 'Սերվերի ներքին սխալ։', 503: 'ERP տվյալների բազան հասանելի չէ։',
    };
    // Текст сервера показываем, только если он армянский; ответы дашборда (вход, доступ — по-русски)
    // и прочее — армянским текстом по коду ответа (как routes_garage.js)
    const HY = /[\u0531-\u058F]/;
    const hyText = (s) => (typeof s === 'string' && HY.test(s) ? s.trim() : '');

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
            throw Object.assign(new Error('Սերվերի հետ կապ չկա։'), { network: true, status: 0, data: null });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (resp.ok && isObj(data) && data.success === true) return data;
        const d = isObj(data) ? data : null;
        // CSRF дашборда («сессия формы устарела») — как routes_learning.js и routes_garage.js
        const msg = (resp.status === 403 && d && d.error === 'csrf' ? 'Էջը հնացել է՝ թարմացրեք այն և կրկնեք։' : '')
            || (d && hyText(d.error))
            || (d && isObj(d.errors) ? Object.values(d.errors).map(hyText).filter(Boolean).join('; ') : '')
            || HTTP_TEXT[resp.status] || ('Սերվերի սխալ (կոդ ' + resp.status + ')։');
        throw Object.assign(new Error(msg), { status: resp.status, data: d });
    }
    const sentence = (s) => { const t = String(s || '').trim(); return !t || /[.!?…։]$/.test(t) ? t : t + '։'; };

    // ---------- Слова вместо жаргона (словарь брифа) ----------
    const minDay = () => num(state.settings && state.settings.min_day_revenue);
    // «100 000 դրամ» — порог дня; без настроек — «օրական նորմը»; minFrom — с отложительным падежом («…-ից պակաս»)
    const minText = () => (minDay() !== null ? fmt(minDay()) + NB + 'դրամ' : 'օրական նորմը');
    const minFrom = () => (minDay() !== null ? fmt(minDay()) + NB + 'դրամից' : 'օրական նորմից');
    const workdays = () => {
        const w = state.settings && Array.isArray(state.settings.workdays) ? state.settings.workdays.map(Number) : null;
        return new Set(w && w.length ? w : [1, 2, 3, 4, 5, 6]);
    };
    // «երկ», «երկ և հնգ», «երկ, չրք և ուրբ»
    function daysList(days) {
        const names = days.map(d => WD_LOWER[d] || String(d));
        return names.length <= 1 ? (names[0] || '') : names.slice(0, -1).join(', ') + ' և ' + names[names.length - 1];
    }
    const uniqSorted = (list) => [...new Set(list)].sort((a, b) => a - b);
    const pairsOf = (p) => arr(p).filter(Array.isArray).map(x => [num(x[0]) || 1, num(x[1])]).filter(x => x[1] !== null);
    // Визиты словами (как сервер и Excel — день и запятая): «երք, ամեն շաբաթ», «հնգ, 2 շաբաթը մեկ», «երկ և հնգ, ամեն շաբաթ»; пусто — «չայցելել»
    function patternHuman(p) {
        const pairs = pairsOf(p);
        if (!pairs.length) return 'չայցելել';
        const w1 = uniqSorted(pairs.filter(x => x[0] === 1).map(x => x[1]));
        const w2 = uniqSorted(pairs.filter(x => x[0] === 2).map(x => x[1]));
        if (w1.join() === w2.join()) return daysList(w1) + ', ամեն շաբաթ';
        if (!w2.length) return daysList(w1) + ', 2 շաբաթը մեկ';
        if (!w1.length) return daysList(w2) + ', 2 շաբաթը մեկ';
        return daysList(w1) + '՝ 1-ին շաբաթը, ' + daysList(w2) + '՝ 2-րդ շաբաթը';
    }
    // Визит «раз в 2 недели» — в какую неделю двухнедельного цикла (так и в Excel)
    function cycleWeekNote(p) {
        const weeks = uniqSorted(pairsOf(p).map(x => x[0]));
        if (weeks.length !== 1) return null;
        return 'Այցը՝ 2 շաբաթից ' + (weeks[0] === 1 ? '1-ինը' : '2-րդը') + '։';
    }
    function freqHuman(f) {
        const n = num(f);
        if (n === null) return '—';
        if (n <= 0) return 'չայցելել';
        if (Math.abs(n - 0.5) < 1e-9) return '2 շաբաթը մեկ';
        if (Math.abs(n - 1) < 1e-9) return 'շաբաթը մեկ անգամ';
        return 'շաբաթը ' + fmt(n, 1) + ' անգամ';
    }
    // Текст шаблона с сервера («երկ, 2 շաբաթից 1-ինը»; до перевода — «пн, 1-я неделя из 2»; прежняя форма страницы — без
    // запятой) — теми же словами, что и на странице: «երկ, 2 շաբաթը մեկ»
    const humanPlanText = (t) => String(t || '—').replace(/,? (?:ամեն շաբաթ|каждую неделю)/g, ', ամեն շաբաթ')
        .replace(/,? (?:2 շաբաթից (?:1-ինը|2-րդը)|[12]-я неделя из 2)/g, ', 2 շաբաթը մեկ')
        .replace(/հաճախորդը մենեջերի պլանում չէ|клиента нет в плане менеджера/g, 'խանութը մենեջերի պլանում չէ');
    // Как часто магазин заказывает: «մոտավորապես 3 շաբաթը մեկ», «գրեթե ամեն շաբաթ», «շաբաթը 2 անգամ»
    function orderRateText(lam) {
        const l = num(lam);
        if (l === null) return null;
        if (l <= 0) return 'վերջին տարում ոչ մի պատվեր';
        if (l >= 0.95) return 'պատվիրում է շաբաթը ' + fmt(round(l, 1), 1) + ' անգամ';
        const w = Math.round(1 / l);
        return w <= 1 ? 'պատվիրում է գրեթե ամեն շաբաթ' : 'պատվիրում է մոտավորապես ' + w + ' շաբաթը մեկ';
    }
    function monthsAgo(days) {
        const m = Math.floor(days / 30.4);
        if (m >= 12) return 'մեկ տարուց ավելի';
        return m <= 1 ? 'մեկ ամսից ավելի' : m + ' ամսից ավելի';
    }
    // Статус покупок (§15) словами: «դադարել է գնել 79 օր առաջ», «չի գնում 9 ամսից ավելի»
    function silenceText(id) {
        const c = custOf(id) || {}, d = num(c.silent_days);
        if (c.status === 'never') return 'վերջին տարում ոչ մի պատվեր';
        if (c.status === 'lost') return d !== null ? 'չի գնում ' + monthsAgo(d) : 'վաղուց չի գնում';
        if (c.status === 'dormant') return d !== null ? 'դադարել է գնել ' + fmt(d) + NB + 'օր առաջ' : 'դադարել է գնել';
        return null;
    }
    const lostMonths = () => {
        const d = num(state.settings && state.settings.lost_min_days);
        const m = Math.max(1, Math.round((d !== null ? d : 120) / 30));
        return m === 1 ? 'մեկ ամսից' : m + ' ամսից';
    };
    function durText(mins) {
        const m = Math.round(Math.abs(mins));
        if (m < 60) return m + NB + 'րոպե';
        const hh = Math.floor(m / 60), r = m % 60;
        return hh + NB + 'ժ' + (r ? ' ' + r + NB + 'րոպե' : '');
    }
    // Разница в длине дня словами: «գրեթե մեկ ժամով ավելի կարճ», «20 րոպեով ավելի երկար»; меньше 5 минут — null
    function dayShift(minutes) {
        const m = Math.abs(minutes), word = minutes > 0 ? 'ավելի կարճ' : 'ավելի երկար';
        if (m < 5) return null;
        if (m < 40) return Math.max(5, Math.round(m / 5) * 5) + ' րոպեով ' + word;
        if (m < 58) return 'գրեթե մեկ ժամով ' + word;
        if (m < 70) return 'մեկ ժամով ' + word;
        const hh = Math.round(m / 30) / 2;
        return fmt(hh, 1) + ' ժամով ' + word;
    }

    // ---------- Шаг 1 · Посчитать ----------
    const mgrName = (m) => (m && m.name ? String(m.name) : 'Մենեջեր ' + (m ? (m.code || m.agent_id) : ''));

    // Менеджеры вне расчёта (галочка «в расчёте» в настройках) выбрать нельзя — сервер их не посчитает;
    // рядом — ссылка на настройки, где их включают
    const pickable = () => (state.managers || []).filter(m => m.included);

    function renderPicker() {
        const box = $('roMgrs');
        box.textContent = '';
        $('roMgrs').parentElement.querySelector('.ro-quick').hidden = !pickable().length;
        if (!state.managers.length) {
            box.append(h('span', { class: 'ro-muted', text: 'ERP-ում երթուղիներով մենեջերներ չկան։' }));
        } else if (!pickable().length) {
            box.append(h('span', { class: 'ro-muted' }, 'Ոչ մի մենեջեր հաշվարկում չէ — ',
                h('a', { href: '/routes/settings#managers', text: 'միացրեք նրանց կարգավորումներում' }), '։'));
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
            txt.append(h('span', { class: 'x', id: id + 'x' }, 'հաշվարկում չէ · ',
                h('a', { href: '/routes/settings#managers', text: 'միացնել կարգավորումներում' })));
            box.append(h('div', { class: 'ro-mgr-item is-off' },
                h('input', { type: 'checkbox', id, disabled: true, 'aria-describedby': id + 'x',
                    'aria-label': mgrName(m) + ' — հաշվարկում չէ' }),
                txt));
        });
        renderPickCount();
    }

    function renderPickCount() {
        const n = pickable().length, off = state.managers ? state.managers.length - n : 0;
        $('roMgrCount').textContent = n ? 'նշված է՝ ' + state.pick.size + ' / ' + n + (off ? ' · հաշվարկում չէ՝ ' + off : '') : '';
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
            mode: val('roMode', 'days') === 'transfer' ? 'transfer' : 'days',
        };
    }

    // Параметры результата — в форму (перед «Пересчитать»); менеджер, которого с тех пор
    // исключили из расчёта, не выбирается
    function setParams(p) {
        const check = (name, v) => document.querySelectorAll('input[name="' + name + '"]').forEach(r => { r.checked = r.value === v; });
        check('roStart', p.start);
        check('roFreq', p.frequencies);
        check('roMode', p.mode === 'transfer' ? 'transfer' : 'days');
        if (state.managers && Array.isArray(p.agent_ids)) {
            const ok = new Set(pickable().map(m => String(m.agent_id)));
            state.pick = new Set(p.agent_ids.map(String).filter(id => ok.has(id)));
            renderPicker();
        }
    }

    const running = () => !!state.job || state.starting;
    // «≈ 5 վայրկյան» — по длительности последнего расчёта, с запасом до 5 секунд
    function runNote() {
        const s = state.result ? num(state.result.seconds) : null;
        const sec = Math.max(5, Math.ceil((s || 0) / 5) * 5);
        return sec < 60 ? '≈' + NB + sec + NB + 'վայրկյան, ' + RUN_NOTE_TAIL
            : '≈' + NB + Math.ceil(sec / 60) + NB + 'րոպե, ' + RUN_NOTE_TAIL;
    }

    function updateRunState() {
        const busy = running();
        const none = !!state.managers && state.pick.size === 0;
        const btn = $('roRunBtn');
        btn.setAttribute('aria-disabled', busy || none ? 'true' : 'false');
        btn.setAttribute('aria-busy', busy ? 'true' : 'false');
        btn.querySelector('i').className = busy ? 'rt-spin-inline' : 'fas fa-play';
        btn.querySelector('span').textContent = busy ? 'Հաշվում եմ…' : 'Հաշվել առաջարկները';
        const note = $('roRunNote');
        note.textContent = busy ? 'Կարող եք փակել էջը — հաշվարկը կշարունակվի սերվերում'
            : (none ? 'Նշեք գոնե մեկ մենեջեր «Լրացուցիչ» բաժնում' : runNote());
        note.classList.toggle('is-warn', none && !busy);
        ['roMgrFs', 'roModeFs', 'roStartFs', 'roFreqFs'].forEach(id => { $(id).disabled = busy; });
        const rb = $('roRecalcBtn');
        rb.setAttribute('aria-disabled', busy ? 'true' : 'false');
        rb.setAttribute('aria-busy', busy ? 'true' : 'false');
        rb.querySelector('i').className = busy ? 'rt-spin-inline' : 'fas fa-rotate';
        rb.querySelector('span').textContent = busy ? 'Հաշվում եմ…' : (state.dirty ? 'Վերահաշվել՝ հաշվի առնելով որոշումները' : 'Վերահաշվել');
        rb.classList.toggle('rt-btn-primary', state.dirty && !busy);
        rb.classList.toggle('rt-btn-ghost', !(state.dirty && !busy));
        $('roStaleNote').classList.toggle('d-none', !(busy && state.result));
    }

    // После расчёта шаг 1 — одна строка «Посчитано … · Пересчитать»; форма — по «Изменить параметры»
    function renderStep1() {
        const r = state.result, has = !!r;
        $('roStep1').classList.toggle('is-done', has);
        $('roDone').hidden = !has;
        const formOpen = !has || state.paramsOpen;
        $('roForm').hidden = !formOpen;
        const tg = $('roParamsToggle');
        tg.setAttribute('aria-expanded', String(has && state.paramsOpen));
        tg.textContent = state.paramsOpen ? 'Թաքցնել պարամետրերը' : 'Փոխել պարամետրերը';
        if (!has) return;
        const t = parseTime(r.generated_at), n = r.managers.length, p = r.params;
        const parts = [(t ? 'Հաշվված է ' + dayTime(t) : 'Վերջին հաշվարկը') + '՝ ' + n + ' մենեջերի համար'];
        if (p.mode === 'transfer') parts.push('խանութների փոխանցումով մենեջերների միջև');
        if (p.start === 'fresh') parts.push('խանութները բաշխված են զրոյից');
        if (p.frequencies === 'current') parts.push('հաճախականությունը՝ ինչպես հիմա');
        const el = $('roDoneText');
        el.textContent = '';
        el.append(icon('fa-circle-check'), h('span', { text: parts.join(', ') }));
        const snap = parseTime(r.snapshot_as_of);
        if (snap) el.append(tip('ERP-ի տվյալները կարդացվել են ' + dayTime(snap) + (num(r.seconds) !== null ? ', հաշվարկը տևել է ' + duration(r.seconds) : '')
            + '։ «Վերահաշվել»՝ նորից հաշվել նույն մենեջերներով և ձեր որոշումները հաշվի առնելով։', 'երբ է հաշվված'));
    }

    // ---------- Запуск и опрос задачи ----------
    function run(params) {
        if (running()) { announce('Հաշվարկն արդեն ընթանում է — սպասեք ավարտին'); return; }
        if (Array.isArray(params.agent_ids) && !params.agent_ids.length) {
            announce('Նշեք գոնե մեկ մենեջեր');
            $('roMore').open = true;
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
        showRunning('Սկսում եմ հաշվարկը…');
        try {
            const d = await api('POST', '/api/routes/optimize', params);
            const id = d.job_id;
            if ((typeof id !== 'string' || !id) && typeof id !== 'number') throw Object.assign(new Error('Սերվերը չվերադարձրեց առաջադրանքի համարը։'), { status: 200 });
            state.starting = false;
            attach(String(id), false);
            announce('Հաշվարկը սկսվեց');
        } catch (e) {
            state.starting = false;
            const busyId = e.status === 409 && e.data ? (e.data.job_id ?? obj(e.data.job).id) : null;
            if (busyId !== null && busyId !== undefined && busyId !== '') {
                attach(String(busyId), false);   // уже идёт — показываем ход той задачи
                announce('Հաշվարկն արդեն ընթանում է — ցույց եմ տալիս դրա ընթացքը');
                return;
            }
            hideRunning();
            if (e.status === 409) {
                showRunError('Հաշվարկն արդեն ընթանում է — այն սկսել են մեկ այլ ներդիրում կամ մեկ այլ օգտատեր։ Սպասեք ավարտին և սեղմեք «Կրկին ստուգել»։',
                    { retryText: 'Կրկին ստուգել', retry: () => loadLast(true, true) });
                return;
            }
            const why = e.network ? 'սերվերի հետ կապ չկա։ Ստուգեք ցանցը և սեղմեք «Կրկնել»։'
                : (e.status === 503 ? 'ERP տվյալների բազան հասանելի չէ։ Փորձեք մի փոքր ուշ։' : sentence(e.message));
            showRunError('Հաշվարկը չսկսվեց՝ ' + why, { auth: e.status === 401, retry: () => run(params) });
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
        showRunning(resumed ? 'Ստուգում եմ հաշվարկի ընթացքը…' : 'Կարդում եմ տվյալները ERP-ից…');
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
                showNet((e.network ? 'Սերվերի հետ կապ չկա' : 'Սերվերը չպատասխանեց') + ' — հաշվարկի ընթացքը նորից կստուգեմ '
                    + Math.round(wait / 1000) + NB + 'վայրկյանից։ Սերվերում հաշվարկը չի ընդհատվում։');
                job.timer = setTimeout(poll, wait);
                return;
            }
            const quiet = job.resumed && !job.alive && e.status === 404;
            endJob();
            if (quiet) return;   // сохранённая задача давно закончилась — просто забываем её
            showRunError(e.status === 404
                ? 'Հաշվարկի առաջադրանքը չի գտնվել — հնարավոր է՝ սերվերը վերագործարկվել է։ Սկսեք հաշվարկը նորից։'
                : 'Չհաջողվեց պարզել հաշվարկի ընթացքը՝ ' + sentence(e.message), { auth: e.status === 401 });
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
            showRunError('Հաշվարկը չհաջողվեց։ ' + sentence(hyText(info.error) || 'Փորձեք կրկին։'),
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

    // «Հաշվում եմ մենեջերներին՝ 4 / 9 — Имя»: done — сколько менеджеров уже посчитано, agent_code — тот, что считается сейчас
    function showProgress(p) {
        const done = num(p.done), total = num(p.total);
        const code = p.agent_code === null || p.agent_code === undefined ? '' : String(p.agent_code);
        let text;
        if (total === null || total <= 0) {
            text = 'Կարդում եմ տվյալները ERP-ից…';
        } else if (done !== null && done >= total) {
            // режим с передачами: после дней по менеджерам — поиск передач (дольше остального)
            text = state.lastRun && state.lastRun.mode === 'transfer'
                ? 'Փնտրում եմ, թե որ խանութներն է ձեռնտու փոխանցել այլ մենեջերների…' : 'Համեմատում եմ, թե ինչ էր և ինչ կլինի…';
        } else {
            const k = Math.min(total, Math.max(0, done || 0) + 1);
            const who = typeof p.agent_name === 'string' && p.agent_name ? p.agent_name
                : ((code && state.managers && state.managers.find(m => String(m.code) === code)) || {}).name;
            text = 'Հաշվում եմ մենեջերներին՝ ' + k + ' / ' + total + (who ? ' — ' + who : (code ? ' — ' + code : ''));
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
        btn.textContent = o.retryText || 'Կրկնել';
        state.retry = o.retry || (() => run(state.lastRun || readParams()));
        $('roRunError').classList.remove('d-none');
    }
    function hideRunError() { $('roRunError').classList.add('d-none'); }

    // «Пересчитать»: те же менеджеры и параметры, что у показанного результата; решения сервер учтёт сам
    function recalc() {
        if (running()) { announce('Հաշվարկն արդեն ընթանում է — սպասեք ավարտին'); return; }
        if (allChanges().some(c => c._saving)) { announce('Սպասեք, մինչև որոշումները պահպանվեն'); return; }
        const p = state.result ? state.result.params : {};
        const params = {
            agent_ids: Array.isArray(p.agent_ids) ? p.agent_ids : null,
            start: p.start === 'fresh' ? 'fresh' : 'current',
            frequencies: p.frequencies === 'current' ? 'current' : 'sales',
            mode: p.mode === 'transfer' ? 'transfer' : 'days',
        };
        setParams(params);
        if (state.managers && params.agent_ids) params.agent_ids = readParams().agent_ids;   // без исключённых с тех пор
        if (Array.isArray(params.agent_ids) && !params.agent_ids.length) {
            announce('Այս հաշվարկի մենեջերներն այլևս հաշվարկում չեն — նշեք մենեջերներին');
            state.paramsOpen = true;
            renderStep1();
            $('roMore').open = true;
            return;
        }
        start(params);
        const box = $('roProgress');
        scrollTo($('roStep1'));
        box.focus({ preventScroll: true });
    }

    // ---------- Последний расчёт ----------
    // fresh — ждём результат новой задачи: если он новее показанного, решения «свежие» (подсказка снимается)
    async function loadLast(fresh, focus, startedAt) {
        if (!state.result) $('roLoading').classList.remove('d-none');
        $('roLoadError').classList.add('d-none');
        try {
            const d = await api('GET', '/api/routes/optimize/last');
            const res = isObj(d.result) ? d.result : (Array.isArray(d.managers) ? d : null);
            if (!res) throw Object.assign(new Error('Սերվերը դատարկ արդյունք վերադարձրեց։'), { status: 200 });
            // результат задачи мог прийти раньше — более старым не перезаписываем
            const cur = state.result && parseTime(state.result.generated_at), got = parseTime(res.generated_at);
            if (cur && got && got < cur) return;
            const same = !!state.result && String(state.result.generated_at) === String(res.generated_at);
            if (fresh && !same) {
                acceptResult(res, focus, startedAt);
            } else {
                setResult(res);
                if (fresh) announce('Նոր հաշվարկ դեռ չկա — էկրանին վերջինն է');
            }
        } catch (e) {
            if (state.result) {
                if (fresh) announce('Չհաջողվեց ստուգել՝ ' + sentence(e.message));
                return;
            }
            if (e.status === 404) {
                $('roEmpty').classList.remove('d-none');
                if (fresh) announce('Հաշվարկներ դեռ չկան');
            } else {
                $('roLoadErrorText').textContent = 'Չհաջողվեց բեռնել վերջին հաշվարկը։ '
                    + (e.network ? 'Սերվերի հետ կապ չկա — ստուգեք ցանցը և սեղմեք «Կրկնել»։' : sentence(e.message));
                $('roLoadError').classList.remove('d-none');
            }
        } finally {
            $('roLoading').classList.add('d-none');
        }
    }

    // Готовый результат новой задачи: фокус — на шаг 2. Решения до запуска в него вошли; сохранённые
    // уже во время расчёта (startedAt — начало задачи) — нет, и «пересчитайте» остаётся
    function acceptResult(res, focus, startedAt) {
        const stale = !!startedAt && state.lastDecision > startedAt;
        clearDirty();
        state.paramsOpen = false;
        setResult(res);
        if (stale) markDirty();
        const n = allChanges().length;
        announce('Հաշվարկը պատրաստ է՝ ' + (n ? n + ' առաջարկ' : 'փոփոխություններ չկան'));
        const a = document.activeElement;
        if (focus && (!a || a === document.body || $('roProgress').contains(a) || a === $('roRunBtn') || a === $('roRecalcBtn'))) {
            $('roStep2Title').focus({ preventScroll: true });
            scrollTo($('roStep2'));
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
            m.balance = isObj(m.balance) ? { given: obj(m.balance.given), received: obj(m.balance.received) } : null;
            m.days_before = arr(m.days_before).filter(d => isObj(d) && num(d.weekday) !== null);
            m.days_after = arr(m.days_after).filter(d => isObj(d) && num(d.weekday) !== null);
            m.days_before.concat(m.days_after).forEach(d => { d.stops = arr(d.stops).filter(isObj); });
            m.changes = arr(m.changes).filter(c => isObj(c) && c.customer_id !== null && c.customer_id !== undefined);
            m.changes.forEach(c => {
                c.from = obj(c.from);
                c.to = obj(c.to);
                c.effect = obj(c.effect);
                const dec = obj(c.decision);
                c.decision = { pattern: decisionOf(dec.pattern), freq: decisionOf(dec.freq), remove: decisionOf(dec.remove),
                    transfer: decisionOf(dec.transfer) };
                if (c.type === 'transfer') { c.from_agent = num(c.from_agent); c.to_agent = num(c.to_agent); c.effect_from = obj(c.effect_from); c.effect_to = obj(c.effect_to); }
                if (!KINDS[c.type]) c.type = num(c.from.freq) !== num(c.to.freq) ? 'frequency' : 'move';
                c._agent = m.agent_id;
                c._key = m.agent_id + ':' + c.customer_id;
                c._q = searchKey(c.customer_id);
                c._saving = false;
                c._error = '';
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
    const custName = (id) => { const c = custOf(id); return c && c.name ? String(c.name) : 'Խանութ ' + id; };
    const custCode = (id) => { const c = custOf(id); return c && c.code ? String(c.code) : ''; };
    const allChanges = () => (state.result ? state.result.managers.flatMap(m => m.changes) : []);
    const kindsOf = (c) => KINDS[c.type] || KINDS.move;
    const isRemove = (c) => c.type === 'remove';
    const isTransfer = (c) => c.type === 'transfer';
    const transferMode = () => !!state.result && state.result.params.mode === 'transfer';
    // Имя менеджера по id — из результата (все стороны передачи в расчёте), иначе из списка менеджеров
    const agentName = (id) => { const m = id === null || id === undefined ? null : mgrOf(id); return m ? mgrName(m) : 'այլ մենեջեր'; };
    // Передачи, где этот менеджер получает магазин (предложение — в списке отдающего)
    const incomingOf = (m) => allChanges().filter(c => isTransfer(c) && String(c.to_agent) === String(m.agent_id));
    const isSilent = (id) => { const c = custOf(id); return !!c && SILENT.has(c.status); };

    function statusOf(c) {
        const vals = kindsOf(c).map(k => c.decision[k]);
        if (vals.every(v => v === vals[0])) return vals[0] || 'none';
        return 'mixed';
    }
    const acceptedAny = (c) => kindsOf(c).some(k => c.decision[k] === 'accepted');

    // Дни недели шаблона, в которые менеджер не работает (воскресенье)
    const offDaysOf = (p) => { const wd = workdays(); return uniqSorted(pairsOf(p).map(x => x[1]).filter(d => !wd.has(d))); };

    // Группа предложения по смыслу: убрать → вернуть (перестал покупать) → с нерабочего дня → реже →
    // каждую неделю (№30: не реже раза в неделю; и с нерабочего дня — число визитов меняется) → другой день
    function groupOf(c) {
        if (isTransfer(c)) return 'transfer';
        if (isRemove(c)) return 'remove';
        if (isSilent(c.customer_id)) return 'winback';
        const ff = num(c.from.freq), tf = num(c.to.freq);
        if (ff !== null && tf !== null && ff < 1 - 1e-9 && Math.abs(tf - 1) < 1e-9) return 'weekly';
        if (offDaysOf(c.from.pattern).length) return 'offday';
        if (ff !== null && tf !== null && tf < ff - 1e-9) return 'freq';
        if (ff !== null && tf !== null && Math.abs(tf - ff) < 1e-9) return 'move';
        return 'other';
    }

    // Заголовок и причина группы (бриф): «Այցելել 2 շաբաթը մեկ — 60 խանութ» · «Պատվիրում են ավելի հազվադեպ…»
    function groupText(key, list) {
        if (key === 'transfer') return { title: 'Փոխանցել այլ մենեջերի', chip: 'Փոխանցել',
            reason: 'Մեկ այլ մենեջեր ավելի մոտ է անցնում այս խանութներին, կամ այդ օրը նրա պատվերները քիչ են։ '
                + 'Խանութի հետ միասին անցնում են նրա հասույթը և պարտքը։ Որոշումը՝ յուրաքանչյուր խանութի համար առանձին։' };
        if (key === 'incoming') return { title: 'Ստանում է այլ մենեջերներից', chip: 'Ստանում է',
            reason: 'Ծրագիրն առաջարկում է այս խանութները փոխանցել այս մենեջերին։ Որոշել կարող եք այստեղ կամ այն մենեջերի մոտ, ով տալիս է։' };
        if (key === 'freq') {
            const half = list.every(c => Math.abs((num(c.to.freq) || 0) - 0.5) < 1e-9);
            return { title: half ? 'Այցելել 2 շաբաթը մեկ' : 'Այցելել ավելի հազվադեպ', chip: 'Ավելի հազվադեպ',
                reason: 'Պատվիրում են ավելի հազվադեպ, քան դրանց այցելում են — իզուր այցեր։' };
        }
        if (key === 'weekly') return { title: 'Այցելել ամեն շաբաթ', chip: 'Ամեն շաբաթ',
            reason: 'Մենեջերը յուրաքանչյուր խանութ այցելում է առնվազն շաբաթը մեկ անգամ, նույնիսկ եթե այն ավելի հազվադեպ է պատվիրում։' };
        if (key === 'move') return { title: 'Տեղափոխել այլ օր', chip: 'Այլ օր',
            reason: 'Այսպես օրն ավելի խիտ է, իսկ հասույթն ըստ օրերի՝ ավելի հավասարաչափ։' };
        if (key === 'offday') {
            const days = uniqSorted(list.flatMap(c => offDaysOf(c.from.pattern)));
            if (days.length === 1 && WD_FROM[days[0]]) {
                const from = WD_FROM[days[0]];   // «կիրակիից»
                return { title: 'Տեղափոխել ' + from, chip: cap(from), reason: cap(WD_FULL[days[0]]) + '՝ ոչ աշխատանքային օր։' };
            }
            return { title: 'Տեղափոխել ոչ աշխատանքային օրերից', chip: 'Հանգստյան օրերից', reason: 'Այս օրերին մենեջերները չեն աշխատում։' };
        }
        if (key === 'winback') return { title: 'Փորձել վերադարձնել', chip: 'Վերադարձնել',
            reason: 'Նախկինում կանոնավոր գնում էին, հիմա դադարել են։ Ամեն շաբաթ այցելում ենք, որ փորձենք վերադարձնել։' };
        if (key === 'remove') return { title: 'Հանել երթուղուց', chip: 'Հանել',
            reason: 'Չեն գնում ' + lostMonths() + ' ավելի կամ վերջին տարում ոչ մի անգամ չեն գնել։ Որոշումը՝ յուրաքանչյուր խանութի համար առանձին։' };
        return { title: 'Այլ փոփոխություններ', chip: 'Այլ', reason: 'Փոխվում են և՛ օրերը, և՛ այցերի քանակը։' };
    }

    // Почему программа это предлагает — одной-двумя фразами для «?» у магазина
    // Сколько уходит вместе с магазином: «հասույթ՝ ≈ 120 հազ. դրամ ամսում, պարտք՝ 45 հազ. դրամ»
    function moneyText(c) {
        const rev = num(c.revenue_month), debt = num(c.debt);
        const bits = [];
        if (rev !== null) bits.push('հասույթ՝ ≈' + NB + moneyShort(rev) + NB + 'դրամ ամսում');
        if (debt !== null) bits.push(debt > 0 ? 'պարտք՝ ' + moneyShort(debt) + NB + 'դրամ' : (debt < 0 ? 'գերավճար՝ ' + moneyShort(-debt) + NB + 'դրամ' : 'պարտք չկա'));
        return bits.join(', ');
    }

    function transferReason(c) {
        const to = agentName(c.to_agent), frm = agentName(c.from_agent);
        const why = {
            km: 'Այս տարածքում արդեն աշխատում է ' + (mgrOf(c.to_agent) ? to + '-ը' : to) + ' — այսպես ավելի քիչ կմ է ստացվում։',
            weak: to + '՝ այդ օրը պատվերները քիչ են — այս խանութով օրն ավելի ուժեղ կլինի։',
            overload: frm + '՝ օրը ծանրաբեռնված է — այն ավելի կարճ կդառնա։',
            owner: 'Այս փոխանցումը դուք արդեն ընդունել եք։',
        }[c.reason_kind] || 'Առանձին այս փոխանցումը գրեթե ոչինչ չի փոխում — օգուտը գալիս է այլ փոփոխությունների հետ միասին։';
        const money = moneyText(c);
        return why + (money ? ' Խանութի հետ միասին անցնում են՝ ' + money + ' — երկու մենեջերների վաճառքի և պարտքերի պլանները պետք է վերահաշվել։' : '');
    }

    function rowReason(c) {
        if (isTransfer(c)) return transferReason(c);
        const reason = String(c.reason || '');
        if (/օրերն ընդունված են ձեր որոշմամբ|шаблон принят владельцем/.test(reason)) return 'Այս օրերը դուք արդեն ընդունել եք — ծրագիրը պահպանել է դրանք։';
        if (/հաճախականությունն ընդունված է ձեր որոշմամբ|частота принята владельцем/.test(reason)) return 'Այցերի այս հաճախականությունը դուք արդեն ընդունել եք։';
        if (/հեռացումն ընդունված է ձեր որոշմամբ|удаление принято владельцем/.test(reason)) return 'Դուք արդեն որոշել եք հանել այս խանութը երթուղուց։';
        const g = groupOf(c), silent = silenceText(c.customer_id);
        if (g === 'remove') return cap(silent || 'դադարել է գնել') + '։';
        if (g === 'winback') return cap(silent || 'դադարել է գնել') + '։ Ամեն շաբաթ այցելում ենք, որ փորձենք վերադարձնել։';
        const off = offDaysOf(c.from.pattern);
        const offText = off.length ? cap(off.map(d => WD_FULL[d]).join(' և ')) + (off.length > 1 ? '՝ ոչ աշխատանքային օրեր։' : '՝ ոչ աշխատանքային օր։') : '';
        if (g === 'weekly') return 'Յուրաքանչյուր խանութ՝ առնվազն շաբաթը մեկ անգամ, իսկ հիմա այն այցելում են ' + freqHuman(num(c.from.freq)) + '։'
            + (offText ? ' ' + offText : '');
        const ff = num(c.from.freq), tf = num(c.to.freq);
        if (ff !== null && tf !== null && tf < ff - 1e-9) {
            const rate = orderRateText(obj(custOf(c.customer_id)).lam_year);
            const season = /սեզոնին|в сезон/.test(reason) ? ' Սեզոնին ավելի հաճախ է պատվիրում — դա հաշվի է առնված։' : '';
            return (offText ? offText + ' ' : '') + (rate ? cap(rate) : 'Հազվադեպ է պատվիրում') + ', իսկ այն այցելում են ' + freqHuman(ff) + '։' + season;
        }
        return offText || 'Այսպես օրն ավելի խիտ է, իսկ հասույթն ըստ օրերի՝ ավելի հավասարաչափ։';
    }

    // Сколько км в неделю меняется у менеджера, если принять все его предложения (минус — меньше)
    const mgrKmDelta = (m) => {
        const b = m ? num(m.before.manager_km) : null, a = m ? num(m.after.manager_km) : null;
        return b !== null && a !== null ? a - b : null;
    };
    // «≈ 2 ժ», «≈ 45 րոպե» — время округлено, чтобы не казалось точным
    function approxDur(mins) {
        const m = Math.abs(mins);
        if (m >= 60) return fmt(Math.round(m / 30) / 2, 1) + NB + 'ժ';
        return Math.max(5, Math.round(m / 5) * 5) + NB + 'րոպե';
    }

    // Эффект одного предложения, применённого к текущему плану, — коротко (в неделю). Перенос сам по себе
    // может добавить км: выгода — от того, как переставлены все магазины вместе, это и говорим
    // «Армен −5,2 կմ, Гор +1,1 կմ» (в неделю) — эффект передачи по обоим менеджерам
    function transferEffectText(c) {
        const side = (e, id) => {
            const km = num(obj(e).manager_km_week);
            return km !== null && round(km, 1) !== 0 ? agentName(id) + ' ' + (km < 0 ? MINUS : '+') + fmt(Math.abs(km), 1) + NB + 'կմ' : null;
        };
        const bits = [side(c.effect_from, c.from_agent), side(c.effect_to, c.to_agent)].filter(Boolean);
        const weak = num(c.effect.weak_days_week);
        if (weak !== null && Math.abs(weak) >= 0.1) bits.push('թույլ օրեր ' + (weak < 0 ? MINUS : '+') + fmt(Math.abs(weak), 1));
        return bits.length ? 'Առանձին այս փոխանցման ազդեցությունը շաբաթում՝ ' + bits.join(', ') + '։' : 'Առանձին այս փոխանցումը գրեթե չի փոխում կիլոմետրերը։';
    }

    function effectText(c, m) {
        if (isTransfer(c)) return transferEffectText(c);
        const e = c.effect;
        const km = num(e.manager_km_week), mins = num(e.minutes_week), weak = num(e.weak_days_week);
        const bits = [];
        if (km !== null && round(km, 1) !== 0) bits.push((km < 0 ? MINUS : '+') + fmt(Math.abs(km), 1) + NB + 'կմ');
        if (mins !== null && Math.round(mins) !== 0) bits.push((mins < 0 ? MINUS : '+') + durText(mins));
        if (weak !== null && Math.abs(weak) >= 0.1) bits.push('թույլ օրեր ' + (weak < 0 ? MINUS : '+') + fmt(Math.abs(weak), 1));
        let text = bits.length ? 'Առանձին այս փոփոխության ազդեցությունը շաբաթում՝ ' + bits.join(', ') + '։' : 'Առանձին այս փոփոխությունը գրեթե չի փոխում կիլոմետրերը և ժամանակը։';
        const total = mgrKmDelta(m);
        if (km !== null && round(km, 1) > 0 && total !== null && total < -1) {
            text += ' Մյուս փոփոխությունների հետ միասին մենեջերի մոտ ստացվում է ' + MINUS + fmt(-total) + NB + 'կմ շաբաթում։';
        }
        return text;
    }

    // Эффект группы: визиты — точно (сумма частот), время — примерно (сумма по магазинам по одному).
    // Километры по группе не складываем: перенос одного магазина сам по себе может добавить км, выгода — от всех вместе
    function groupEffect(list, m) {
        if (list.length && list.every(isTransfer)) {   // передачи: сколько магазинов и выручки уходит или приходит
            const rev = list.reduce((a, c) => a + (num(c.revenue_month) || 0), 0);
            const debt = list.reduce((a, c) => a + (num(c.debt) || 0), 0);
            const incoming = !!m && String(list[0].to_agent) === String(m.agent_id);
            const n = list.length;
            return { tone: 0,
                text: (incoming ? 'կստանա ' : 'կտա ') + fmt(n) + ' խանութ'
                    + ' — ≈' + NB + moneyShort(rev) + NB + 'դրամ հասույթ ամսում',
                tip: 'Հասույթն ամսական միջինն է՝ ըստ խանութների պատվերների վերջին 12 ամսում։ Այս խանութների պարտքն այսօր '
                    + moneyShort(debt) + NB + 'դրամ է (ինչպես հաճախորդների էջում)։ Կիլոմետրերը խմբով չենք գումարում — '
                    + 'փոխանցման օգուտը կախված է նրանից, թե ինչպես են վերադասավորված բոլոր խանութները միասին։' };
        }
        let dv = 0, mins = 0;
        list.forEach(c => {
            const ff = num(c.from.freq), tf = isRemove(c) ? 0 : num(c.to.freq);
            if (ff !== null && tf !== null) dv += tf - ff;
            const mm = num(c.effect.minutes_week);
            if (mm !== null) mins += mm;
        });
        if (Math.abs(dv) >= 0.25) {
            const n = round(Math.abs(dv), 1);
            return { tone: dv < 0 ? 1 : -1,
                text: 'շաբաթում ' + fmt(n, 1) + ' այցով ' + (dv < 0 ? 'պակաս' : 'ավելի')
                    + (dv < 0 && mins <= -20 ? ' — դա ≈' + NB + approxDur(mins) + ' աշխատանք է' : ''),
                tip: 'Այցերը հաշվված են ճշգրիտ։ Ժամանակը մոտավոր է՝ յուրաքանչյուր խանութի գումարը, եթե դրանք ընդունեք մեկ առ մեկ։' };
        }
        const km = mgrKmDelta(m);
        return { tone: 0, text: 'այցերի քանակը նույնն է — փոխվում են միայն օրերը',
            tip: 'Քանի կիլոմետր կխնայվի, կախված է նրանից, թե ինչպես են վերադասավորված բոլոր խանութները միասին։'
                + (km !== null && km < -1 ? ' Եթե ընդունեք այս մենեջերի բոլոր առաջարկները, նա շաբաթում ' + fmt(-km) + NB + 'կմ-ով պակաս կանցնի։' : '') };
    }

    function setResult(raw) {
        const prev = state.result;
        const res = normalizeResult(raw);
        state.result = res;
        state.byAgent = new Map(res.managers.map(m => [String(m.agent_id), m]));
        state.dirty = readDirty();
        if (state.panelAgent && !state.byAgent.has(state.panelAgent)) state.panelAgent = null;
        if (!prev || String(prev.generated_at) !== String(res.generated_at)) state.pf = { group: 'all', q: '', raw: '' };
        $('roEmpty').classList.add('d-none');
        $('roLoadError').classList.add('d-none');
        $('roLoading').classList.add('d-none');
        $('roResult').classList.remove('d-none');
        renderStep1();
        renderTexts();
        renderMapSection();
        renderStep3();
        renderDecisions();
        renderRecalc();
        updateRunState();
        loadDecisions();       // расчёт мог отметить решения отработавшими — список и число для Excel заново
    }

    // Всё, что зависит от настроек (порог дня, рабочие дни) и сезонов, — заново
    function renderTexts() {
        renderMain();
        renderKpis();
        renderAllTable();
        renderCards();
        renderPanel();
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
        $('roRecalc').hidden = !state.dirty || !state.result;
        updateRunState();
    }

    // ---------- Шаг 2 · Главное ----------
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
    const SEASONS = [
        { key: 'revenue_week_low', mkey: 'revenue_low', word: 'ձմռանը' },
        { key: 'revenue_week_peak', mkey: 'revenue_peak', word: 'ամռանը' },
        { key: 'revenue_week_year', mkey: 'revenue_year', word: 'տարեկան միջինը' },
    ];
    const truckTipText = () => 'Մեքենան ամրացված է վարորդին, ոչ թե մենեջերին։ Բոլոր մենեջերների օրվա պատվերներն առաքվում են միասին՝ մեքենաներով '
        + 'և երթերով, հաշվի առնելով տոննաժը և մեքենայի աշխատանքային օրը։ Ուստի տարբեր մենեջերների այցի օրերը կապված են — եթե մեկ տարածքի պատվերներն '
        + 'ընկնում են առաքման նույն օրվա վրա, մեքենան այնտեղ գնում է մեկ անգամ։ Լիտրեր = երթի կմ × այն տանող մեքենայի ծախսը, տարեկան միջինով։ '
        + 'Երթերը, բեռնվածությունը և ավտոպարկի կմ-ը՝ «Բոլոր թվերը» բաժնում։';
    const kmTipText = () => 'Մենեջերների հաշվարկային վազքը շաբաթում՝ տնից դեպի օրվա խանութները գտնված կարճ հերթականությամբ և հետ՝ տուն։ '
        + (state.roads ? 'Կմ-ը հաշվված է քարտեզի ճանապարհներով։' : 'Կմ-ը հաշվված է ուղիղ գծով՝ ճանապարհների ոլորունության ճշգրտումով։')
        + ' Լիտրերը և դրամները՝ ըստ մենեջերների մեքենաների ծախսի և կարգավորումներում նշված վառելիքի գների։';
    function weakTipText() {
        const months = monthsText(state.season && state.season.low_months);
        const days = num(state.result.after.days_total) ?? num(state.result.before.days_total);
        return 'Հաշվում ենք ըստ ձմռան պատվերների' + (months ? ' (' + months + ')' : '') + ' — դա տարվա ամենաթույլ ժամանակն է։ Օրը թույլ է, '
            + 'եթե այն հավանաբար չի հավաքի ' + minText() + ' (հավանականությունը 50%-ից պակաս է)։'
            + (days !== null ? ' Մենեջերների աշխատանքային օրերը՝ ընդամենը ' + fmt(days, 1) + ' շաբաթում։' : '');
    }

    function renderMain() {
        const r = state.result, list = $('roMainList'), note = $('roMainNote');
        list.textContent = '';
        note.textContent = '';
        note.hidden = true;
        const items = [];
        const n = allChanges().length;
        $('roMainLead').textContent = n ? 'Ըստ հաշվարկի, եթե ընդունեք բոլոր առաջարկները՝' : 'Հաշվարկը ձեռնտու փոփոխություններ չգտավ։';

        // 0. Дизель грузовиков — первой строкой (парк: заказы всех менеджеров развозят машины вместе)
        const tlb = num(r.before.truck_liters_week), tla = num(r.after.truck_liters_week);
        const link = (hash, text) => h('a', { href: '/routes/settings#' + hash, text });
        if (tlb !== null && tla !== null) {
            const dl = tlb - tla, ab = num(r.before.truck_amd_week), aa = num(r.after.truck_amd_week);
            const money = ab !== null && aa !== null ? ' (≈' + NB + moneyRound(ab) + ' → ' + moneyRound(aa) + NB + 'դրամ)' : '';
            items.push({ tone: Math.abs(dl) < 1 ? 0 : (dl > 0 ? 1 : -1), tip: truckTipText(), tipLabel: 'բեռնատարների դիզել',
                nodes: Math.abs(dl) < 1
                    ? ['բեռնատարների դիզելը գրեթե չի փոխվի — ', mark(fmt(tla) + NB + 'լ շաբաթում', 0), money]
                    : ['բեռնատարների դիզել՝ ', mark(fmt(tlb) + ' → ' + fmt(tla) + NB + 'լ շաբաթում', dl > 0 ? 1 : -1),
                        money, ' — ' + fmt(Math.abs(dl)) + NB + 'լ-ով ' + (dl > 0 ? 'պակաս' : 'ավելի')] });
        } else {
            note.append(icon('fa-truck'), h('span', {}, depotOf()
                ? ['Նշեք մեքենաների տոննաժը և ծախսը ', link('trucks', 'կարգավորումներում'), ' — այդ դեպքում ծրագիրը կխնայի բեռնատարների դիզելը։']
                : ['Նշեք պահեստը և մեքենաները ', link('depot', 'կարգավորումներում'), ' — այդ դեպքում ծրագիրը կխնայի բեռնատարների դիզելը։']));
            note.hidden = false;
        }

        // 1. Километры менеджеров: литры и драмы, если посчитаны
        const kb = total('before', 'manager_km_week', 'manager_km'), ka = total('after', 'manager_km_week', 'manager_km');
        if (kb !== null && ka !== null) {
            const d = kb - ka;
            if (Math.abs(d) < 1) {
                items.push({ tone: 0, nodes: ['մենեջերների վազքը գրեթե չի փոխվի — ', mark(fmt(ka) + NB + 'կմ շաբաթում', 0)], tip: kmTipText(), tipLabel: 'կիլոմետրեր' });
            } else {
                const extra = [];
                const lb = num(r.before.manager_liters_week), la = num(r.after.manager_liters_week);
                if (lb !== null && la !== null && Math.abs(lb - la) >= 1) extra.push('≈' + NB + fmt(Math.abs(lb - la)) + NB + 'լ վառելիք');
                const ab = num(r.before.manager_amd_week), aa = num(r.after.manager_amd_week);
                if (ab !== null && aa !== null && Math.abs(ab - aa) >= 100) extra.push('≈' + NB + moneyRound(Math.abs(ab - aa)) + NB + 'դրամ');
                items.push({ tone: d > 0 ? 1 : -1, tip: kmTipText(), tipLabel: 'կիլոմետրեր',
                    nodes: ['մենեջերները կանցնեն ', mark('շաբաթում ' + fmt(Math.abs(d)) + NB + 'կմ-ով ' + (d > 0 ? 'պակաս' : 'ավելի'), d > 0 ? 1 : -1),
                        extra.length ? ' (' + extra.join(', ') + ')' : ''] });
            }
        }

        // 2. Слабые дни зимой
        const wb = total('before', 'days_below_min', 'days_below_min'), wa = total('after', 'days_below_min', 'days_below_min');
        if (wb !== null && wa !== null) {
            const phrase = 'ձմռանն այն օրերը, երբ հասույթը պակաս է ' + minFrom() + ', ';
            const rb = round(wb, 1), ra = round(wa, 1);
            if (ra < rb) items.push({ tone: 1, nodes: [phrase + 'կլինեն ', mark(fmt(wa, 1) + '՝ ' + fmt(wb, 1) + '-ի փոխարեն', 1)], tip: weakTipText(), tipLabel: 'թույլ օրեր' });
            else if (ra > rb) items.push({ tone: -1, nodes: [phrase + 'կշատանան՝ ', mark(fmt(wa, 1) + '՝ ' + fmt(wb, 1) + '-ի փոխարեն', -1)], tip: weakTipText(), tipLabel: 'թույլ օրեր' });
            else if (!ra) items.push({ tone: 1, nodes: [phrase, mark('չկան և չեն լինի', 1)], tip: weakTipText(), tipLabel: 'թույլ օրեր' });
            else items.push({ tone: 0, nodes: [phrase + 'կմնան նույնքան — ', mark(fmt(wa, 1), 0)], tip: weakTipText(), tipLabel: 'թույլ օրեր' });
        }

        // 3. Визиты и длина рабочего дня
        const vb = total('before', 'visits_week', 'visits'), va = total('after', 'visits_week', 'visits');
        const pb = total('before', 'avg_plan_hours', 'avg_plan_hours', true), pa = total('after', 'avg_plan_hours', 'avg_plan_hours', true);
        if (vb !== null && va !== null && vb > 0) {
            const dv = vb - va, p = Math.round(Math.abs(dv) / vb * 100);
            const shift = pb !== null && pa !== null ? dayShift((pb - pa) * 60) : null;
            const nodes = [];
            if (Math.abs(dv) < 0.5) nodes.push('այցերի քանակը կմնա նույնը — ', mark(fmt(va) + ' շաբաթում', 0));
            else nodes.push('այցերը կլինեն ', mark((p ? p + '%-ով' : fmt(Math.abs(dv), 1) + '-ով') + ' ' + (dv > 0 ? 'պակաս' : 'ավելի'), dv > 0 ? 1 : -1),
                ' — շաբաթում ' + fmt(va, 1) + '՝ ' + fmt(vb, 1) + '-ի փոխարեն');
            if (shift) nodes.push(', իսկ աշխատանքային օրը՝ ' + shift);
            items.push({ tone: dv > 0.4 ? 1 : (dv < -0.4 ? -1 : 0), nodes, tipLabel: 'այցեր',
                tip: 'Այցը՝ մենեջերի մեկ մուտքը խանութ։ Աշխատանքային օրը՝ տնից մեկնելուց մինչև վերադարձ, միջինում'
                    + (pb !== null && pa !== null ? '՝ հիմա ' + fmt(pb, 1) + NB + 'ժ, կլինի ' + fmt(pa, 1) + NB + 'ժ։' : '։') });
        }

        // 4. Выручка — не меняется или меняется
        const rows = SEASONS.map(s => ({ s, b: total('before', s.key, s.mkey), a: total('after', s.key, s.mkey) }))
            .filter(x => x.b !== null && x.a !== null);
        if (rows.length) {
            const lost = rows.filter(x => x.a < x.b - 1);   // 1 դրամ — округление
            const now = rows.map(x => x.s.word + ' ≈' + NB + moneyShort(x.b)).join(', ');
            const tipText = 'Ծրագիրը նվազեցնում է հաճախականությունը միայն այնտեղ, որտեղ խանութն ավելի հազվադեպ է պատվիրում, քան այն այցելում են, ուստի սպասվող հասույթը չի նվազում։ '
                + 'Սպասվող հասույթը շաբաթում հիմա՝ ' + now + ' դրամ։ Գնումները դադարեցրած խանութներից հասույթ արդեն չկա — դրանց այցերն ապարդյուն են։';
            if (!lost.length) items.push({ tone: 1, nodes: [mark('հասույթը չի նվազի', 1), ' — ո՛չ ձմռանը, ո՛չ ամռանը'], tip: tipText, tipLabel: 'հասույթ' });
            else items.push({ tone: -1, tip: tipText, tipLabel: 'հասույթ',
                nodes: ['սպասվող հասույթը կնվազի՝ ', mark(lost.map(x => x.s.word + ' ' + fmt(Math.abs((x.a - x.b) / x.b * 100), 1) + '%-ով').join(', '), -1)] });
        }

        // 5. Передачи между менеджерами — одной строкой (только в расчёте с передачами)
        if (transferMode()) {
            const t = obj(r.transfers), nt = num(t.count) || 0;
            const tipText = 'Խանութն առաջարկվում է փոխանցել այլ մենեջերի, եթե նա առանց այդ էլ անցնում է մոտակայքով, կամ այդ օրը նրա պատվերները քիչ են։ '
                + 'Փոխանցումը պետք է նկատելի խնայողություն տա՝ շաբաթում ' + fmt(num(t.penalty_week) ?? 2000) + NB + 'դրամից ավելի, այլապես խանութի հետ կապը խզելն իմաստ չունի։ '
                + 'Ընկերության հասույթը փոխանցումներից չի փոխվում, բայց փոխվում են մենեջերների վաճառքի և պարտքերի պլանները — թե ով որքան է տալիս և ստանում, տեսեք մենեջերների քարտերում։';
            if (nt) {
                items.push({ tone: 1, tip: tipText, tipLabel: 'փոխանցումներ',
                    nodes: [mark('փոխանցել ' + fmt(nt) + ' խանութ', 1),
                        ' այլ մենեջերների (≈' + NB + moneyShort(t.revenue_month) + NB + 'դրամ հասույթ ամսում) — սա արդեն հաշվի է առնված վերևի թվերում'] });
            } else {
                items.push({ tone: 0, tip: tipText, tipLabel: 'փոխանցումներ',
                    nodes: ['խանութներ փոխանցել մենեջերների միջև ', mark('ձեռնտու չէ', 0), ' — ոչ մի փոխանցում նկատելի խնայողություն չի տալիս'] });
            }
        }

        items.forEach(it => list.append(h('li', { class: it.tone > 0 ? 'is-good' : (it.tone < 0 ? 'is-bad' : 'is-same') },
            h('span', { class: 'ico', 'aria-hidden': 'true' }, icon(it.tone > 0 ? 'fa-check' : (it.tone < 0 ? 'fa-arrow-up' : 'fa-equals'))),
            h('span', { class: 'txt' }, it.nodes, it.tip ? [' ', tip(it.tip, it.tipLabel)] : null))));
        const quality = $('roQualityWarnings');
        quality.textContent = '';
        const warn = text => quality.append(h('p', { class: 'ro-warn' }, icon('fa-triangle-exclamation'), h('span', { text })));
        if (r.time_gate && !r.time_gate.ok) {
            warn('Պլանը չի տեղավորվում աշխատանքային օրվա մեջ՝ ' + fmt((r.time_gate.days || []).length)
                + ' օր արտաժամով։ Ստուգեք ամրացված օրերը և հաճախականությունները, ապա վերահաշվեք պլանը։');
        }
        const validation = r.forecast_validation;
        if (!validation || validation.status !== 'checked') {
            warn('Կանխատեսման ճշտությունը դեռ ստուգված չէ։ Վերահաշվեք պլանը վաճառքի պատմությամբ։');
        } else {
            const poor = (validation.managers || []).filter(m => m.supported && !m.ok);
            if (poor.length) warn('Հասույթի կանխատեսումը ստուգման կարիք ունի՝ '
                + poor.map(m => m.code + ' (' + signed(m.error_pct,1) + '%)').join(', ')
                + '։ Շեղումները չափված են 4 ավարտված շաբաթների վրա, պլանի հասույթը գնահատական է։');
        }
    }

    // ---------- Три крупных показателя ----------
    function kpiNode(o) {
        const both = o.was !== null && o.now !== null;
        const diff = both ? o.now - o.was : null;
        const t = both ? (round(diff, o.d) < 0 ? -1 : (round(diff, o.d) > 0 ? 1 : 0)) : 0;
        const good = o.lower ? -t : t;
        const pctText = both && o.was ? ' (' + signed(diff / Math.abs(o.was) * 100, 0) + '%)' : '';
        const unit = both && o.unit ? NB + o.unit(Math.abs(round(diff, o.d))) : '';
        return h('div', { class: 'rt-kpi' + (good > 0 ? ' is-good' : (good < 0 ? ' is-bad' : '')) },
            h('div', { class: 'rt-kpi-label' }, h('span', { text: o.label }), o.tip ? tip(o.tip, o.label) : null),
            h('div', { class: 'rt-kpi-val' },
                h('span', { class: 'rt-sr-only', text: 'հիմա ' }), h('span', { class: 'was', text: fmt(o.was, o.d) }),
                h('span', { class: 'arr', 'aria-hidden': 'true', text: '→' }),
                h('span', { class: 'rt-sr-only', text: ', եթե ընդունեք — ' }), h('span', { class: 'now', text: fmt(o.now, o.d) })),
            h('div', { class: 'rt-kpi-delta' }, both ? (t ? signed(diff, o.d) + unit + pctText : 'առանց փոփոխության') : 'հաշվված չէ'));
    }

    function renderKpis() {
        const box = $('roKpis');
        box.textContent = '';
        // слово к разнице (после числа — ед. ч.): «−412 կմ», «−14 օր», «−465 այց»
        box.append(
            kpiNode({ label: 'Կմ շաբաթում', was: total('before', 'manager_km_week', 'manager_km'), now: total('after', 'manager_km_week', 'manager_km'),
                d: 0, lower: true, unit: () => 'կմ', tip: kmTipText() }),
            kpiNode({ label: 'Թույլ օրեր ձմռանը', was: total('before', 'days_below_min', 'days_below_min'), now: total('after', 'days_below_min', 'days_below_min'),
                d: 1, lower: true, unit: () => 'օր', tip: 'Թույլ օր՝ օր, երբ հասույթը պակաս է ' + minFrom() + '։ ' + weakTipText() }),
            kpiNode({ label: 'Այցեր շաբաթում', was: total('before', 'visits_week', 'visits'), now: total('after', 'visits_week', 'visits'),
                d: 1, lower: true, unit: () => 'այց', tip: 'Քանի անգամ են մենեջերները շաբաթում մտնում խանութներ՝ հաշվարկի բոլոր մենեջերներով։ '
                    + '«2 շաբաթը մեկ» այցը հաշվվում է որպես կես այց շաբաթում։' }));
    }

    // ---------- «Все цифры» ----------
    function renderAllTable() {
        const r = state.result, tb = $('roAllTable').tBodies[0];
        tb.textContent = '';
        const days = num(r.after.days_total) ?? num(r.before.days_total);
        const rows = [
            { label: 'Այցեր շաբաթում', b: total('before', 'visits_week', 'visits'), a: total('after', 'visits_week', 'visits'), d: 1, lower: true },
            { label: 'Ձմռան օրեր՝ ' + minFrom() + ' պակաս հասույթով' + (days !== null ? ' (' + fmt(days, 1) + '-ից)' : ''),
                b: total('before', 'days_below_min', 'days_below_min'), a: total('after', 'days_below_min', 'days_below_min'), d: 1, lower: true },
            { label: 'Մենեջերների կմ շաբաթում', b: total('before', 'manager_km_week', 'manager_km'), a: total('after', 'manager_km_week', 'manager_km'), d: 0, lower: true },
            { label: 'Մենեջերների վառելիք, լիտր շաբաթում', b: num(r.before.manager_liters_week), a: num(r.after.manager_liters_week), d: 0, lower: true },
            { label: 'Մենեջերների վառելիք, դրամ շաբաթում', b: num(r.before.manager_amd_week), a: num(r.after.manager_amd_week), d: 0, lower: true,
                none: 'հաշվված չէ — վառելիքի գներ չկան' },
            { label: 'Բեռնատարների դիզել, լիտր շաբաթում', b: num(r.before.truck_liters_week), a: num(r.after.truck_liters_week),
                d: 0, lower: true, none: 'հաշվված չէ — նշեք մեքենաների տոննաժը և ծախսը' },
            { label: 'Բեռնատարների դիզել, դրամ շաբաթում', b: num(r.before.truck_amd_week), a: num(r.after.truck_amd_week), d: 0, lower: true,
                none: 'հաշվված չէ — մեքենաներ կամ դիզելի գին չկա' },
            { label: 'Բեռնատարների մաշվածք, դրամ շաբաթում', b: num(r.before.truck_wear_amd_week), a: num(r.after.truck_wear_amd_week), d: 0, lower: true },
            { label: 'Բեռնատարների դիզել և մաշվածք, դրամ շաբաթում', b: num(r.before.truck_operating_amd_week), a: num(r.after.truck_operating_amd_week), d: 0, lower: true,
                none: 'հաշվված չէ — մեքենաներ կամ դիզելի գին չկա' },
            { label: 'Բեռնատարների կմ շաբաթում', b: num(r.before.truck_km_week), a: num(r.after.truck_km_week), d: 0, lower: true,
                none: 'հաշվված չէ — պահեստ կամ մեքենաներ չկան' },
            { label: 'Բեռնատարների երթեր շաբաթում', b: num(r.before.trips_week), a: num(r.after.trips_week), d: 1, lower: true, none: 'հաշվված չէ' },
            { label: 'Երթեր առաքման օրում', b: num(r.before.trips_per_day), a: num(r.after.trips_per_day), d: 1, lower: true, none: 'հաշվված չէ' },
            { label: 'Մեքենայի բեռնվածությունը երթում, %', b: num(r.before.avg_load_pct), a: num(r.after.avg_load_pct), d: 0, lower: false, none: 'հաշվված չէ' },
            { label: 'Բեռնվածությունը բարձր սեզոնին (ամռանը), %', b: num(r.before.avg_load_pct_peak), a: num(r.after.avg_load_pct_peak), d: 0, lower: null, none: 'հաշվված չէ' },
            { label: 'Մեքենա-ժամ շաբաթում', b: num(r.before.truck_hours_week), a: num(r.after.truck_hours_week), d: 0, lower: true, none: 'հաշվված չէ' },
            { label: 'Առաքման օրեր, երբ բարձր սեզոնին մեքենաները չեն հասցնում', b: num(r.before.truck_days_short_week), a: num(r.after.truck_days_short_week),
                d: 1, lower: true, none: 'հաշվված չէ' },
            { label: 'Ժամանակ խանութներում, ժամ օրում', b: total('before', 'avg_plan_work_hours', 'avg_work_hours', true),
                a: total('after', 'avg_plan_work_hours', 'avg_work_hours', true), d: 1, lower: null },
            { label: 'Աշխատանքային օր՝ տնից ճանապարհով, ժամ', b: total('before', 'avg_plan_hours', 'avg_plan_hours', true),
                a: total('after', 'avg_plan_hours', 'avg_plan_hours', true), d: 1, lower: true },
        ].concat(SEASONS.map(s => ({ label: 'Սպասվող հասույթ շաբաթում (' + s.word + '), դրամ', b: total('before', s.key, s.mkey),
            a: total('after', s.key, s.mkey), money: true, lower: false })));
        rows.forEach(x => {
            const both = x.b !== null && x.a !== null;
            const f = (v) => (x.money ? moneyShort(v) : fmt(v, x.d));
            let diff = '—', cls = '';
            if (both) {
                const dd = x.a - x.b, rr = x.money ? Math.round(dd / 1000) : round(dd, x.d);
                if (!rr) diff = 'առանց փոփոխության';
                else {
                    diff = (dd > 0 ? '+' : MINUS) + (x.money ? moneyShort(Math.abs(dd)) : fmt(Math.abs(dd), x.d))
                        + (x.b ? ' (' + signed(dd / Math.abs(x.b) * 100, 0) + '%)' : '');
                    if (x.lower !== null) cls = ((dd < 0) === !!x.lower) ? 'is-good' : 'is-bad';
                }
            }
            tb.append(h('tr', {},
                h('th', { scope: 'row', text: x.label }),
                both || x.b !== null || x.a !== null
                    ? [h('td', { class: 'num', text: f(x.b) }), h('td', { class: 'num', text: f(x.a) }), h('td', { class: 'num ' + cls, text: diff })]
                    : h('td', { class: 'muted', colspan: '3', text: x.none || 'հաշվված չէ' })));
        });
        // Сколько предложений в каких группах и откуда данные
        const cnt = {};
        allChanges().forEach(c => { const g = groupOf(c); cnt[g] = (cnt[g] || 0) + 1; });
        const words = { transfer: 'փոխանցել այլ մենեջերի', freq: 'այցելել ավելի հազվադեպ', weekly: 'ամեն շաբաթ', move: 'այլ օր', offday: 'ոչ աշխատանքային օրից',
            winback: 'վերադարձնել', remove: 'հանել', other: 'այլ' };
        const foot = $('roAllFoot');
        foot.textContent = '';
        const n = allChanges().length;
        const snap = parseTime(r.snapshot_as_of);
        foot.append((n ? 'Առաջարկներ՝ ' + fmt(n) + ' — ' + GROUP_ORDER.filter(g => cnt[g]).map(g => words[g] + ' ' + fmt(cnt[g])).join(', ') + '։ '
            : 'Առաջարկներ չկան։ ') + (snap ? 'ERP-ի տվյալները՝ ' + dayTime(snap) + ' դրությամբ։' : ''));
        // Дизель грузовиков по полной модели (ответ владельца №33): удержан ли и сколько изменений снято
        const g = r.fleet_gate;
        if (g) {
            const lit = (v) => fmt(v, 1) + NB + 'լ/շաբ.';
            if (!g.ok) {
                foot.append(h('strong', { class: 'ro-warn', text: ' Բեռնատարների ծախսերի սահմանափակումը չհաջողվեց պահել՝ դիզելն էր ' + lit(g.liters_before)
                    + ', դարձավ ' + lit(g.liters_after) + '։ Մնացորդը պարտադիր փոփոխություններից է (ամեն շաբաթ, կիրակիից տեղափոխում, ձեր որոշումները)։' }));
            } else if (g.reverted > 0) {
                foot.append(' Բեռնատարների դիզելը չի աճում՝ էր ' + lit(g.liters_before) + ', դարձավ ' + lit(g.liters_after) + '։ '
                    + (r.params && r.params.mode === 'transfer'
                        ? 'Մեքենաների ծախսերը մեծացնող փոխանցումներ չեն առաջարկվում։'
                        : fmt(g.reverted) + NB + 'տեղափոխում չի առաջարկվում — դրանք կմեծացնեին մեքենաների ծախսերը։'));
            }
            if (num(g.operating_before_amd) !== null) foot.append(' Դիզելը և մաշվածքն ըստ լրիվ մոդելի՝ '
                + fmt(g.operating_before_amd) + ' → ' + fmt(g.operating_after_amd) + NB + 'դրամ/շաբ.');
        }
        if (r.after.truck_fuel_load_unconfigured || r.after.truck_wear_unconfigured) foot.append(
            ' Մեքենաների մի մասի համար նշված չեն բեռից կախված վառելիքի նորմերը կամ մաշվածքի արժեքը։ ',
            h('a', { href: '/routes/settings#trucks', text: 'Լրացնել մեքենաների նորմերը' }), '։');
        if (r.fuel_price_source === 'fallback') {
            foot.append(' Վառելիքի գները կարգավորումներում նշված չեն — օրերն ընտրելիս ծրագիրը վերցրել է պայմանական գին՝ '
                + fmt(r.fuel_price_used) + NB + 'դրամ/լ։ ', h('a', { href: '/routes/settings#fuel', text: 'Նշել գները' }), '։');
        }
        const validation = r.forecast_validation;
        if (validation) {
            if (validation.status === 'checked') {
                foot.append(' Հասույթի ստուգում 4 ավարտված շաբաթների վրա՝ ±'
                    + fmt(validation.tolerance_pct) + '%-ի սահմաններում է ' + fmt(validation.passed) + ' / '
                    + fmt(validation.total) + ' մենեջերի մոտ։');
            }
        }
    }

    // ---------- Карточки менеджеров ----------
    function mgrProgress(m) {
        const st = { accepted: 0, rejected: 0, mixed: 0, none: 0 };
        m.changes.forEach(c => { st[statusOf(c)]++; });
        return st;
    }

    function feasText(m) {
        const f = m.feasibility, max = num(f.max_days_ge_min), wd = num(f.workdays);
        if (f.reachable !== false || max === null || !wd) return null;
        const k = Math.max(0, Math.min(Math.floor(max), wd));
        if (k === 0) return 'Ձմռանը պատվերները չեն բավարարում նույնիսկ մեկ օր ' + minText() + ' հավաքելու համար — պետք են նոր խանութներ։';
        return 'Ձմռանը պատվերները բավարարում են ' + wd + ' օրից ' + k + '-ի համար — որպեսզի ամեն օր բերի '
            + minText() + ', պետք են նոր խանութներ։';
    }

    // Баланс передач менеджера (ответ владельца №17): «տալիս է 3 խանութ (≈ 450 հազ. դրամ ամսում) · ստանում է 1 խանութ (…)»
    function balanceNode(m) {
        if (!transferMode() || !m.balance) return null;
        const g = m.balance.given, r = m.balance.received;
        const ng = num(g.stores) || 0, nr = num(r.stores) || 0;
        if (!ng && !nr) return h('p', { class: 'ro-card-bal is-none', text: 'Խանութներ չի տալիս և չի ստանում' });
        const part = (word, n, x) => word + ' ' + fmt(n) + ' խանութ'
            + ' (≈' + NB + moneyShort(x.revenue_month) + NB + 'դրամ ամսում)';
        const text = [ng ? part('տալիս է', ng, g) : null, nr ? part('ստանում է', nr, r) : null].filter(Boolean).join(' · ');
        const debt = (x) => moneyShort(num(x.debt) || 0) + NB + 'դրամ';
        return h('p', { class: 'ro-card-bal' }, icon('fa-people-arrows'), h('span', { text: cap(text) }),
            tip('Եթե ընդունեք այս մենեջերի բոլոր փոխանցումները։ Հասույթն ամսական միջինն է՝ ըստ խանութների պատվերների 12 ամսում։ Խանութների պարտքը՝ '
                + (ng ? 'գնում է ' + debt(g) : '') + (ng && nr ? ', ' : '') + (nr ? 'գալիս է ' + debt(r) : '')
                + '։ Այս թվերով վերահաշվեք վաճառքի և պարտքերի պլանները։', 'փոխանցումների հաշվեկշիռ'));
    }

    function cardNode(m) {
        const n = m.changes.length, open = state.panelAgent === String(m.agent_id);
        const sum = [];
        const kb = num(m.before.manager_km), ka = num(m.after.manager_km);
        if (kb !== null && ka !== null) {
            const d = ka - kb;
            sum.push(Math.abs(d) < 1 ? h('span', { text: 'կմ-ը գրեթե առանց փոփոխության' })
                : h('span', { class: d < 0 ? 'is-good' : 'is-bad', text: (d < 0 ? MINUS : '+') + fmt(Math.abs(d)) + NB + 'կմ շաբաթում' }));
        }
        const wb = num(m.before.days_below_min), wa = num(m.after.days_below_min);
        if (wb !== null && wa !== null) {
            sum.push(!wb && !wa ? h('span', { text: 'թույլ օրեր չկան' })
                : h('span', { class: wa < wb ? 'is-good' : (wa > wb ? 'is-bad' : ''), text: 'թույլ օրեր՝ ' + fmt(wb, 1) + ' → ' + fmt(wa, 1) }));
        }
        sum.push(h('span', { text: n ? fmt(n) + ' առաջարկ' : 'փոփոխություններ չկան' }));
        const st = mgrProgress(m), decided = st.accepted + st.rejected + st.mixed;
        const warn = feasText(m);
        const bal = balanceNode(m);
        const btn = (n || m.hints.length || incomingOf(m).length) ? h('button', { type: 'button', class: 'rt-btn ' + (open ? 'rt-btn-primary' : 'rt-btn-ghost') + ' ro-card-btn',
            'aria-expanded': String(open), 'aria-controls': 'roPanel', dataset: { open: String(m.agent_id) },
            'aria-label': (open ? 'Ծալել առաջարկները՝ ' : 'Դիտել առաջարկները՝ ') + mgrName(m) },
            h('span', { text: open ? 'Ծալել առաջարկները' : (n || incomingOf(m).length ? 'Դիտել առաջարկները' : 'Դիտել հուշումները') }),
            icon(open ? 'fa-chevron-up' : 'fa-arrow-down')) : null;
        return h('li', { class: 'ro-card' + (open ? ' is-open' : ''), dataset: { agent: String(m.agent_id) } },
            h('div', { class: 'ro-card-head' },
                h('span', { class: 'rt-dot', style: 'background:' + m._color, 'aria-hidden': 'true' }),
                h('span', { class: 'ro-card-name', text: mgrName(m) }),
                m.code ? h('span', { class: 'ro-card-code', text: String(m.code) }) : null),
            h('p', { class: 'ro-card-sum' }, sum.map((x, i) => (i ? [h('span', { class: 'sep', 'aria-hidden': 'true', text: ' · ' }), x] : x))),
            bal,
            warn ? h('p', { class: 'ro-card-warn' }, icon('fa-triangle-exclamation'), h('span', { text: warn })) : null,
            m.time_capped === true ? h('p', { class: 'ro-card-note', text: 'Հաշվարկը կանգնեցվել է ժամանակի սահմանով — կրկնված հաշվարկը կարող է մի փոքր տարբերվել։' }) : null,
            n ? h('div', { class: 'ro-card-prog' },
                h('span', { class: 'bar', 'aria-hidden': 'true' },
                    h('span', { class: 'ok', style: 'width:' + ((st.accepted + st.mixed) / n * 100).toFixed(1) + '%' }),
                    h('span', { class: 'off', style: 'width:' + (st.rejected / n * 100).toFixed(1) + '%' })),
                h('span', { class: 'txt', text: decided ? 'որոշված է՝ ' + fmt(decided) + ' / ' + fmt(n) : 'որոշումներ դեռ չկան' })) : null,
            btn);
    }

    function renderCards() {
        const ul = $('roCards');
        ul.textContent = '';
        if (!state.result.managers.length) {
            ul.append(h('li', { class: 'rt-placeholder', text: 'Հաշվարկում մենեջերներ չկան։' }));
            return;
        }
        state.result.managers.forEach(m => ul.append(cardNode(m)));
    }

    // Карточка перерисовывается целиком (прогресс, кнопка); фокус на её кнопке сохраняется
    function refreshCard(agentId) {
        const m = state.byAgent.get(String(agentId));
        const old = $('roCards').querySelector('.ro-card[data-agent="' + String(agentId) + '"]');
        if (!m || !old) return;
        const hadFocus = old.contains(document.activeElement);
        const fresh = cardNode(m);
        old.replaceWith(fresh);
        if (hadFocus) { const b = fresh.querySelector('button[data-open]'); if (b) b.focus({ preventScroll: true }); }
    }

    function onCardsClick(ev) {
        const b = ev.target.closest('button[data-open]');
        if (!b) return;
        if (state.panelAgent === b.dataset.open) closePanel(true);
        else openPanel(b.dataset.open);
    }

    // ---------- Предложения менеджера ----------
    const panelMgr = () => (state.panelAgent ? state.byAgent.get(state.panelAgent) || null : null);

    function openPanel(agentId) {
        const prev = state.panelAgent;
        state.panelAgent = String(agentId);
        if (prev !== state.panelAgent) state.pf = { group: 'all', q: '', raw: '' };
        renderPanel();
        if (prev) refreshCard(prev);
        refreshCard(state.panelAgent);
        const panel = $('roPanel');
        scrollTo(panel);
        const t = $('roPanelTitle');
        if (t) t.focus({ preventScroll: true });
        const m = panelMgr();
        announce('Բացված են առաջարկները՝ ' + mgrName(m));
    }

    function closePanel(focusCard) {
        const prev = state.panelAgent;
        state.panelAgent = null;
        renderPanel();
        if (!prev) return;
        refreshCard(prev);
        if (focusCard) {
            const b = $('roCards').querySelector('.ro-card[data-agent="' + prev + '"] button[data-open]');
            if (b) { b.focus({ preventScroll: true }); b.closest('.ro-card').scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'nearest' }); }
        }
    }

    const passQuery = (q) => !state.pf.q || q.includes(state.pf.q);
    // «Принять все в группе»: только без решения, не «убрать» и не «передать» (решение по каждому магазину)
    const bulkable = (c) => !c._saving && !isRemove(c) && !isTransfer(c) && statusOf(c) === 'none' && passQuery(c._q);

    function renderPanel() {
        const box = $('roPanel');
        box.textContent = '';
        state.rows = new Map();
        state.pgroups = [];
        const m = panelMgr();
        if (!m) { box.hidden = true; return; }
        box.hidden = false;
        box.style.setProperty('--mgr', m._color);

        // группы по смыслу, внутри — по названию магазина
        const by = new Map();
        m.changes.forEach(c => {
            const g = groupOf(c);
            if (!by.has(g)) by.set(g, []);
            by.get(g).push(c);
        });
        const inc = incomingOf(m);   // передают этому менеджеру — то же решение, что у отдающего
        if (inc.length) by.set('incoming', inc);
        const total = m.changes.length + inc.length;
        const keys = GROUP_ORDER.filter(k => by.has(k));
        keys.forEach(k => by.get(k).sort((a, b) => custName(a.customer_id).localeCompare(custName(b.customer_id), 'ru')));
        if (!['all'].concat(keys, m.hints.length ? ['hints'] : []).includes(state.pf.group)) state.pf.group = 'all';

        box.append(h('div', { class: 'ro-panel-head' },
            h('h3', { class: 'ro-panel-title', id: 'roPanelTitle', tabindex: '-1' },
                h('span', { class: 'rt-dot', style: 'background:' + m._color, 'aria-hidden': 'true' }),
                h('span', { class: 'pre', text: 'Առաջարկներ — ' }), h('span', { class: 'n', text: mgrName(m) }),
                m.code ? h('span', { class: 'c', text: String(m.code) }) : null),
            h('div', { class: 'ro-panel-acts' },
                h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { pact: 'map' }, 'aria-label': 'Ցույց տալ քարտեզում՝ ' + mgrName(m) },
                    icon('fa-map-location-dot'), 'Քարտեզում'),
                h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { pact: 'close' }, 'aria-label': 'Ծալել առաջարկները՝ ' + mgrName(m) },
                    icon('fa-xmark'), 'Ծալել'))));
        state.panelStats = h('p', { class: 'ro-panel-stats' });
        box.append(state.panelStats);

        if (!total && !m.hints.length) {
            box.append(h('p', { class: 'rt-placeholder', text: 'Ծրագիրը ոչինչ չի առաջարկում փոխել այս մենեջերի մոտ։' }));
            renderPanelStats();
            return;
        }

        // Переключатели групп и поиск
        const chips = h('div', { class: 'rt-chips ro-gchips', role: 'group', 'aria-label': 'Որ առաջարկները ցույց տալ' },
            h('button', { type: 'button', class: 'rt-chip', dataset: { pgroup: 'all' } }, 'Բոլորը', h('span', { class: 'cnt', text: fmt(total) })),
            keys.map(k => h('button', { type: 'button', class: 'rt-chip g-' + k, dataset: { pgroup: k } },
                groupText(k, by.get(k)).chip, h('span', { class: 'cnt', text: fmt(by.get(k).length) }))),
            m.hints.length ? h('button', { type: 'button', class: 'rt-chip g-hints', dataset: { pgroup: 'hints' } }, 'Հուշումներ',
                h('span', { class: 'cnt', text: fmt(m.hints.length) })) : null);
        const search = h('label', { class: 'ro-search', for: 'roPanelSearch' },
            icon('fa-magnifying-glass'), h('span', { class: 'rt-sr-only', text: 'Գտնել խանութ՝ ըստ անվանման կամ կոդի' }),
            h('input', { type: 'search', id: 'roPanelSearch', class: 'rt-input', placeholder: 'Գտնել խանութ…', autocomplete: 'off', spellcheck: 'false' }));
        box.append(h('div', { class: 'ro-panel-tools' }, chips, search));
        search.querySelector('input').value = state.pf.raw || '';   // панель перерисовали — поиск остаётся

        const wrap = h('div', { class: 'ro-grps' });
        keys.forEach(k => wrap.append(groupNode(m, k, by.get(k))));
        if (m.hints.length) wrap.append(hintsNode(m));
        state.noMatch = h('p', { class: 'rt-placeholder', hidden: true, text: 'Ոչինչ չի գտնվել — ստուգեք խանութի անվանումը կամ կոդը։' });
        wrap.append(state.noMatch);
        box.append(wrap);
        applyPanelFilter();
    }

    function groupNode(m, key, list) {
        const t = groupText(key, list);
        const id = 'roG_' + key;
        const g = { key, list, busy: false, open: state.pf.group === key };
        g.count = h('span', { class: 'ro-grp-count' });
        g.state = h('span', { class: 'ro-grp-state' });
        g.listEl = h('ul', { class: 'ro-list', id: id + '_l', 'aria-label': t.title, hidden: true });
        list.forEach(c => g.listEl.append(rowNode(c, m)));
        g.toggle = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost', 'aria-expanded': 'false', 'aria-controls': id + '_l', dataset: { gact: 'list', g: key } },
            icon('fa-chevron-down'), h('span'));
        g.accept = ONE_BY_ONE.has(key) ? null : h('button', { type: 'button', class: 'rt-btn ro-btn-ok', dataset: { gact: 'accept', g: key } },
            icon('fa-check-double'), h('span'));
        const eff = groupEffect(list, m);
        g.el = h('section', { class: 'ro-grp g-' + key, 'aria-labelledby': id + '_t', dataset: { g: key } },
            h('div', { class: 'ro-grp-head' },
                h('span', { class: 'ro-grp-ico', 'aria-hidden': 'true' }, icon(GROUP_ICON[key] || 'fa-pen')),
                h('div', { class: 'ro-grp-titles' },
                    h('h4', { class: 'ro-grp-title', id: id + '_t' }, t.title, ' — ', g.count),
                    h('p', { class: 'ro-grp-reason', text: t.reason }),
                    h('p', { class: 'ro-grp-eff' + (eff.tone > 0 ? ' is-good' : (eff.tone < 0 ? ' is-bad' : '')) },
                        'Եթե ընդունեք ամբողջ խումբը՝ ', h('b', { text: eff.text }), ' ', tip(eff.tip, 'խմբի ազդեցությունը'))),
                g.state),
            h('div', { class: 'ro-grp-acts' }, g.accept, g.toggle),
            g.listEl);
        state.pgroups.push(g);
        return g.el;
    }

    function hintsNode(m) {
        const id = 'roG_hints';
        const g = { key: 'hints', list: [], hints: m.hints, open: state.pf.group === 'hints' };
        g.count = h('span', { class: 'ro-grp-count' });
        g.state = h('span', { class: 'ro-grp-state' });
        g.listEl = h('ul', { class: 'ro-list', id: id + '_l', 'aria-label': 'Հուշումներ', hidden: true });
        g.hintRows = m.hints.map(x => {
            const code = custCode(x.customer_id);
            const li = h('li', { class: 'ro-item is-hint' },
                h('div', { class: 'ro-item-main' },
                    h('div', { class: 'ro-item-name' }, h('span', { class: 'n', text: custName(x.customer_id) }), code ? h('span', { class: 'c', text: code }) : null),
                    h('div', { class: 'ro-item-change', text: cap(String(x.text || '')) })));
            g.listEl.append(li);
            return { el: li, q: x._q };
        });
        g.toggle = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost', 'aria-expanded': 'false', 'aria-controls': id + '_l', dataset: { gact: 'list', g: 'hints' } },
            icon('fa-chevron-down'), h('span'));
        g.el = h('section', { class: 'ro-grp g-hints', 'aria-labelledby': id + '_t', dataset: { g: 'hints' } },
            h('div', { class: 'ro-grp-head' },
                h('span', { class: 'ro-grp-ico', 'aria-hidden': 'true' }, icon(GROUP_ICON.hints)),
                h('div', { class: 'ro-grp-titles' },
                    h('h4', { class: 'ro-grp-title', id: id + '_t' }, 'Հուշումներ', ' — ', g.count),
                    h('p', { class: 'ro-grp-reason', text: 'Այս խանութներն ավելի հաճախ են պատվիրում, քան դրանց այցելում են։ Ծրագիրը դա չի փոխում — որոշեք ինքներդ։' })),
                g.state),
            h('div', { class: 'ro-grp-acts' }, g.toggle),
            g.listEl);
        state.pgroups.push(g);
        return g.el;
    }

    function rowNode(c, m) {
        const id = c.customer_id, code = custCode(id), silent = silenceText(id), transfer = isTransfer(c);
        const details = [rowReason(c), isRemove(c) ? null : cycleWeekNote(c.to.pattern), effectText(c, m)].filter(Boolean).join(' ');
        const main = h('div', { class: 'ro-item-main' },
            h('div', { class: 'ro-item-name' }, h('span', { class: 'n', text: custName(id) }), code ? h('span', { class: 'c', text: code }) : null,
                silent ? h('span', { class: 'tag', text: silent }) : null),
            h('div', { class: 'ro-item-change' },
                h('span', { class: 'lbl', text: 'էր՝ ' }), h('span', { class: 'from', text: (transfer ? agentName(c.from_agent) + '՝ ' : '') + patternHuman(c.from.pattern) }),
                h('span', { class: 'arr', 'aria-hidden': 'true', text: ' → ' }),
                h('span', { class: 'lbl', text: 'կլինի՝ ' }),
                h('span', { class: 'to', text: isRemove(c) ? 'չայցելել' : (transfer ? agentName(c.to_agent) + '՝ ' : '') + patternHuman(c.to.pattern) }),
                ' ', tip(details, 'ինչու՝ ' + custName(id))),
            transfer && moneyText(c) ? h('div', { class: 'ro-item-note', text: cap(moneyText(c)) }) : null);
        const side = h('div', { class: 'ro-item-side' });
        const el = h('li', { class: 'ro-item', dataset: { key: c._key } }, main, side);
        state.rows.set(c._key, { el, side, c });
        refreshRow(c);
        return el;
    }

    // Состояние и кнопки строки. Фокус был на кнопке строки — переходит на главную кнопку нового состояния.
    // «Убрать из маршрута»: «Убрать…» (с подтверждением) — принять, «Оставить» — отклонить;
    // «Передать другому менеджеру»: «Передать…» (с подтверждением), «Оставить», «На карте»
    function refreshRow(c) {
        const r = state.rows.get(c._key);
        if (!r) return;
        const st = statusOf(c), remove = isRemove(c), transfer = isTransfer(c);
        r.el.className = 'ro-item' + (remove ? ' is-remove' : '') + (transfer ? ' is-transfer' : '') + (st === 'none' ? '' : ' is-' + st);
        r.el.setAttribute('aria-busy', c._saving ? 'true' : 'false');
        const hadFocus = r.side.contains(document.activeElement);
        r.side.textContent = '';
        const name = custName(c.customer_id);
        const btn = (cls, ico, text, act, label) => h('button', { type: 'button', class: 'rt-btn rt-btn-sm ' + cls, dataset: { act, key: c._key },
            'aria-disabled': c._saving ? 'true' : null, 'aria-label': (label || text) + '՝ ' + name }, ico ? icon(ico) : null, text);
        const S = st === 'none' ? null : ((remove ? REMOVE_ROW_STATUS[st] : (transfer ? TRANSFER_ROW_STATUS[st] : ROW_STATUS[st])) || ROW_STATUS.mixed);
        if (c._saving) r.side.append(h('span', { class: 'ro-pill' }, spin(), 'Պահպանում եմ…'));
        else if (S) r.side.append(h('span', { class: 'ro-pill ' + S.cls }, icon(S.icon), S.text));
        const acts = h('div', { class: 'ro-item-acts' });
        if (remove) {
            if (st === 'none') acts.append(btn('ro-btn-remove', 'fa-user-minus', 'Հանել…', 'accept', 'Հանել երթուղուց'),
                btn('rt-btn-ghost', null, 'Թողնել', 'reject', 'Թողնել երթուղում'));
            else acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Չեղարկել', 'reset', 'Չեղարկել որոշումը'));
        } else if (transfer) {
            if (st === 'none') acts.append(btn('ro-btn-ok', 'fa-people-arrows', 'Փոխանցել…', 'accept', 'Փոխանցել ' + agentName(c.to_agent) + ' մենեջերին'),
                btn('rt-btn-ghost', null, 'Թողնել', 'reject', 'Թողնել ' + agentName(c.from_agent) + ' մենեջերի մոտ'));
            else acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Չեղարկել', 'reset', 'Չեղարկել որոշումը'));
            acts.append(btn('rt-btn-ghost', 'fa-map-location-dot', 'Քարտեզում', 'map', 'Ցույց տալ փոխանցումը քարտեզում'));
        } else {
            if (st === 'none' || st === 'mixed') acts.append(btn('ro-btn-ok', 'fa-check', 'Ընդունել', 'accept'));
            if (st === 'none') acts.append(btn('rt-btn-ghost', null, 'Թողնել ինչպես կա', 'reject'));
            if (st !== 'none') acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Չեղարկել', 'reset', 'Չեղարկել որոշումը'));
        }
        r.side.append(acts);
        if (c._error) r.side.append(h('div', { class: 'ro-item-err', role: 'alert', text: c._error }));
        if (hadFocus) {
            const b = acts.querySelector(st === 'none' ? '[data-act="accept"]' : '[data-act="reset"]') || acts.querySelector('button');
            if (b) b.focus({ preventScroll: true });
        }
    }

    function setGroupOpen(g, open) {
        g.open = open;
        g.listEl.hidden = !open;
        g.toggle.setAttribute('aria-expanded', String(open));
    }

    // Заголовки групп: число, сколько решено, кнопки; фильтр (группа, поиск) — что видно
    function applyPanelFilter() {
        const f = state.pf, searching = !!f.q;
        let shown = 0;
        state.pgroups.forEach(g => {
            let n = 0;
            if (g.key === 'hints') {
                g.hintRows.forEach(x => { const ok = passQuery(x.q); x.el.hidden = !ok; if (ok) n++; });
            } else {
                g.list.forEach(c => { const r = state.rows.get(c._key), ok = passQuery(c._q); if (r) r.el.hidden = !ok; if (ok) n++; });
            }
            g.shown = n;
            const visible = (f.group === 'all' || f.group === g.key) && (!searching || n > 0);
            g.el.hidden = !visible;
            if (visible && (searching || f.group === g.key)) setGroupOpen(g, true);
            if (visible) shown += n;
            renderGroupHead(g);
        });
        if (state.noMatch) state.noMatch.hidden = !(searching && !shown);
        document.querySelectorAll('#roPanel .ro-gchips .rt-chip').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.pgroup === f.group)));
        renderPanelStats();
    }

    function renderGroupHead(g) {
        const total = g.key === 'hints' ? g.hintRows.length : g.list.length;
        const n = state.pf.q ? g.shown : total;
        g.count.textContent = (state.pf.q ? 'գտնվել է՝ ' : '') + fmt(n) + ' խանութ';
        g.toggle.querySelector('span').textContent = g.open ? 'Թաքցնել խանութները' : 'Ցույց տալ խանութները';
        if (g.key === 'hints') { g.state.textContent = 'ի գիտություն'; return; }
        let acc = 0, rej = 0, pending = 0;
        g.list.forEach(c => {
            const st = statusOf(c);
            if (st === 'accepted' || st === 'mixed') acc++;
            else if (st === 'rejected') rej++;
            if (bulkable(c)) pending++;
        });
        g.state.textContent = '';
        if (acc + rej === total) g.state.append(icon('fa-circle-check'), ' որոշված է բոլորի համար');
        else g.state.append('ընդունված է՝ ' + fmt(acc) + ' / ' + fmt(total) + (rej ? ' · թողնված է՝ ' + fmt(rej) : ''));
        g.state.classList.toggle('is-done', acc + rej === total);
        if (g.accept) {
            const word = state.pf.q ? 'Ընդունել գտնվածները' : 'Ընդունել ամբողջ խումբը';
            g.accept.querySelector('span').textContent = g.busy ? 'Պահպանում եմ…' : word + ' (' + fmt(pending) + ')';
            g.accept.setAttribute('aria-disabled', g.busy || !pending ? 'true' : 'false');
            g.accept.hidden = !pending && !g.busy;
            g.accept.setAttribute('aria-label', word + '՝ ' + groupText(g.key, g.list).title + ', առանց որոշման՝ ' + pending);
        }
    }

    function renderPanelStats() {
        const m = panelMgr(), el = state.panelStats;
        if (!m || !el) return;
        const n = m.changes.length, st = mgrProgress(m), inc = incomingOf(m).length;
        el.textContent = '';
        const incText = inc ? 'ստանում է այլ մենեջերներից ' + fmt(inc) + ' խանութ' : '';
        if (!n) { el.append('Սեփական առաջարկներ չկան', inc ? ' — ' + incText + '։' : (m.hints.length ? ' — միայն հուշումներ։' : '։')); return; }
        el.append(h('b', { text: fmt(n) }), ' առաջարկ');
        if (st.accepted + st.mixed) el.append(' · ընդունված է՝ ', h('b', { text: fmt(st.accepted + st.mixed) }));
        if (st.rejected) el.append(' · թողնված է ինչպես կա՝ ', h('b', { text: fmt(st.rejected) }));
        if (inc) el.append(' · ' + incText);
        el.append('։ Ընդունեք խումբն ամբողջությամբ կամ բացեք խանութները և որոշեք յուրաքանչյուրի համար։');
    }

    function refreshPanelHeads() {
        state.pgroups.forEach(renderGroupHead);
        renderPanelStats();
    }

    function onPanelClick(ev) {
        const b = ev.target.closest('button');
        if (!b || b.getAttribute('aria-disabled') === 'true') return;
        const m = panelMgr();
        if (b.dataset.act) {
            const r = state.rows.get(b.dataset.key);
            if (!r) return;
            if (b.dataset.act === 'map') { showTransfer(r.c); return; }
            if (b.dataset.act === 'accept' && isTransfer(r.c)) {
                const money = moneyText(r.c);
                const ok = window.confirm('Փոխանցե՞լ «' + custName(r.c.customer_id) + '» խանութը ' + agentName(r.c.from_agent) + ' մենեջերից '
                    + agentName(r.c.to_agent) + ' մենեջերին։\n\n'
                    + (money ? 'Խանութի հետ միասին անցնում են՝ ' + money + ' — վերահաշվեք երկու մենեջերների վաճառքի և պարտքերի պլանները։ ' : '')
                    + 'ERP-ում ոչինչ չի փոխվի, քանի դեռ ինքներդ չեք մուտքագրել պլանը։ Որոշումը կարելի է չեղարկել։');
                if (!ok) return;
            }
            if (b.dataset.act === 'accept' && isRemove(r.c)) {
                const ok = window.confirm('Հանե՞լ «' + custName(r.c.customer_id) + '» խանութը երթուղուց։\n\n'
                    + 'Մենեջերն այլևս չի այցելի այս խանութ։ ERP-ում ոչինչ չի փոխվի, քանի դեռ ինքներդ չեք մուտքագրել պլանը։ '
                    + 'Որոշումը կարելի է չեղարկել։');
                if (!ok) return;
            }
            decide([r.c], b.dataset.act);
            return;
        }
        if (b.dataset.gact) {
            const g = state.pgroups.find(x => x.key === b.dataset.g);
            if (!g) return;
            if (b.dataset.gact === 'list') { setGroupOpen(g, !g.open); renderGroupHead(g); return; }
            if (b.dataset.gact === 'accept') acceptGroup(g);
            return;
        }
        if (b.dataset.pgroup) {
            state.pf.group = b.dataset.pgroup;
            applyPanelFilter();
            return;
        }
        if (b.dataset.pact === 'close') closePanel(true);
        else if (b.dataset.pact === 'map' && m) showOnMap(m);
    }

    function onPanelSearch(ev) {
        if (ev.target.id !== 'roPanelSearch') return;
        const q = norm(ev.target.value.trim());
        state.pf.raw = ev.target.value;
        if (q === state.pf.q) return;
        state.pf.q = q;
        applyPanelFilter();
    }

    async function acceptGroup(g) {
        if (g.busy) return;
        const list = g.list.filter(bulkable);
        if (!list.length) { announce('Այս խմբում առանց որոշման առաջարկներ չկան'); return; }
        g.busy = true;
        g.accept.querySelector('i').className = 'rt-spin-inline';
        renderGroupHead(g);
        const hadFocus = document.activeElement === g.accept;
        try {
            await decide(list, 'accept');
        } finally {
            g.busy = false;
            g.accept.querySelector('i').className = 'fas fa-check-double';
            renderGroupHead(g);
        }
        // всё в группе решено — кнопка «Принять все» исчезла, фокус переходит на «Показать магазины»
        if (hadFocus && g.accept.hidden) g.toggle.focus({ preventScroll: true });
    }

    // ---------- Решения: оптимистично, одним запросом, с откатом при ошибке ----------
    const whyText = (err) => (err.network ? 'սերվերի հետ կապ չկա' : (err.status === 401 ? 'մուտք գործեք նորից'
        : String(err.message || 'սերվերի սխալ').replace(/[.!։]\s*$/, '')));

    // Тело решения: «было» (from) — строка «было» предложения: решение привязано к плану, на котором
    // принималось, и если план клиента в ERP потом изменится, оно не применится молча
    function decisionItem(c, kind, action) {
        if (kind === 'remove') {   // «убрать из маршрута»: значение — «без визитов», было — шаблон дней
            return { customer_id: c.customer_id, agent_id: c._agent, kind, value: [], from: c.from.pattern, action };
        }
        if (kind === 'transfer') {   // «передать»: кому и в какие дни, было — шаблон дней у своего менеджера
            return { customer_id: c.customer_id, agent_id: c._agent, kind, value: { agent_id: c.to_agent, pattern: c.to.pattern },
                from: c.from.pattern, action };
        }
        const side = kind === 'pattern' ? 'pattern' : 'freq';
        return { customer_id: c.customer_id, agent_id: c._agent, kind, value: c.to[side], from: c.from[side], action };
    }

    // Все выбранные предложения (и оба вида у «другой день и реже») — одним запросом, одной транзакцией:
    // сохранилось всё или ничего, поэтому при ошибке откатываются все
    async function decide(list, action) {
        if (!(action in ACTION_STATUS)) return;
        const target = ACTION_STATUS[action];
        const todo = [];
        list.forEach(c => {
            if (c._saving) return;
            // отправляем только те виды решения, что меняются (у «частично» — недостающий)
            const kinds = kindsOf(c).filter(k => c.decision[k] !== target);
            if (!kinds.length) return;
            c._prev = Object.assign({}, c.decision);
            c._kinds = kinds;
            kinds.forEach(k => { c.decision[k] = target; });
            c._saving = true;
            c._error = '';
            refreshRow(c);
            todo.push(c);
        });
        if (!todo.length) return;
        afterDecisions(todo);
        const owners = [];
        const items = [];
        todo.forEach(c => c._kinds.forEach(k => { items.push(decisionItem(c, k, action)); owners.push(c); }));
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
                const mm = /^items\.(\d+)/.exec(k);
                if (mm && owners[+mm[1]] && !rowErr.has(owners[+mm[1]])) rowErr.set(owners[+mm[1]], String(v));
            });
        }
        todo.forEach(c => {
            if (err) {
                c._kinds.forEach(k => { c.decision[k] = decisionOf(c._prev[k]); });
                const own = rowErr.get(c);
                c._error = own ? 'Չպահպանվեց՝ ' + own.replace(/[.!։]\s*$/, '') + '։'
                    : (rowErr.size ? 'Չպահպանվեց մյուսների հետ միասին — ցուցակում սխալ կա։ Սեղմեք նորից։'
                        : 'Չպահպանվեց՝ ' + whyText(err) + '։ Սեղմեք նորից։');
            }
            c._saving = false;
            refreshRow(c);
        });
        if (!err) {
            state.lastDecision = Date.now();
            markDirty();
        }
        afterDecisions(todo);
        loadDecisions();
        const one = todo.length === 1 && isRemove(todo[0]), moved = todo.length === 1 && isTransfer(todo[0]);
        const done = (one ? { accept: 'Կհանվի երթուղուց', reject: 'Մնում է երթուղում', reset: 'Որոշումը չեղարկվեց' }
            : (moved ? { accept: 'Կփոխանցվի', reject: 'Մնում է իր մենեջերի մոտ', reset: 'Որոշումը չեղարկվեց' }
                : { accept: 'Ընդունված է', reject: 'Թողնված է ինչպես կա', reset: 'Որոշումը չեղարկվեց' }))[action];
        const who = '՝ ' + (todo.length === 1 ? custName(todo[0].customer_id) : fmt(todo.length));
        announce(err ? 'Չպահպանվեց' + who + '։ ' + sentence(cap(whyText(err))) : done + who);
    }

    function afterDecisions(list) {
        new Set(list.map(c => String(c._agent))).forEach(refreshCard);
        refreshPanelHeads();
        renderStep3();
        renderRecalc();
    }

    // ---------- Шаг 3 · Принять и выгрузить ----------
    function renderStep3() {
        const has = !!state.result;
        $('roStep3Empty').hidden = has;
        $('roStep3Body').hidden = !has;
        if (!has) return;
        let acc = 0;
        const mgrs = new Set();
        allChanges().forEach(c => { if (acceptedAny(c)) { acc++; mgrs.add(String(c._agent)); } });
        const el = $('roAccepted');
        el.textContent = '';
        if (acc) {
            el.append(icon('fa-circle-check'), h('span', {}, 'Դուք ընդունել եք ', h('b', { text: fmt(acc) + ' փոփոխություն' }),
                ' ' + mgrs.size + ' մենեջերի մոտ։'));
            el.classList.add('is-done');
        } else {
            el.append(h('span', { text: 'Դուք դեռ ոչինչ չեք ընդունել։ Բացեք մենեջերին 2-րդ քայլում և սեղմեք «Ընդունել» — ընդունվածը կներառվի ERP-ի համար պլանում։' }));
            el.classList.remove('is-done');
        }
        renderExport();
        renderStepWarn();
    }

    // Одна-две строки простыми словами: решения устарели, данные ERP обновились
    function renderStepWarn() {
        const box = $('roStepWarn');
        box.textContent = '';
        if (!state.result) return;
        const s = state.decisions ? state.decisions.summary : null;
        const stale = Math.max(state.result.stale_decisions.length, s ? num(s.stale) || 0 : 0);
        const line = (...kids) => box.append(h('p', { class: 'ro-warn' }, icon('fa-triangle-exclamation'), h('span', {}, kids)));
        if (stale) {
            line(h('b', { text: fmt(stale) + ' որոշում այլևս չի գործում' }),
                '՝ այս խանութների պլանը ERP-ում փոխվել է ձեր որոշումից հետո, ուստի ' + (stale === 1 ? 'այն չի մտնում' : 'դրանք չեն մտնում')
                + ' ո՛չ հաշվարկի, ո՛չ ֆայլի մեջ։ ', h('button', { type: 'button', class: 'rt-linkbtn', dataset: { showdec: '1' }, text: 'Ցույց տալ' }));
        }
        const mm = state.decisions ? dataMismatch(state.decisions.data_as_of) : null;
        if (mm) line('ERP-ի տվյալները թարմացվել են հաշվարկից հետո (հաշվարկը՝ ' + stamp(mm.calc) + ' դրությամբ, հիմա՝ ' + stamp(mm.now)
            + ' դրությամբ)։ Սեղմեք «Վերահաշվել» 1-ին քայլում, որպեսզի առաջարկները համընկնեն ընթացիկ պլանի հետ։');
    }

    // ---------- Ваши решения ----------
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
            state.decError = e.network ? 'սերվերի հետ կապ չկա' : whyText(e);
        }
        renderDecisions();
        renderExport();
        renderStepWarn();
    }

    const decName = (x) => (x.customer_name ? String(x.customer_name) : 'Խանութ ' + x.customer_id);
    const dataMismatch = (asOf) => {
        const a = parseTime(asOf), b = state.result ? parseTime(state.result.snapshot_as_of) : null;
        return a && b && a.getTime() !== b.getTime() ? { now: a, calc: b } : null;
    };

    function renderDecisions() {
        const d = state.decisions, counts = $('roDecCounts'), list = $('roDecList'), reset = $('roDecReset');
        counts.textContent = '';
        list.textContent = '';
        const errBox = $('roDecErr');
        errBox.classList.toggle('d-none', !state.decError);
        errBox.textContent = state.decError ? 'Որոշումների ցուցակը չբեռնվեց՝ ' + state.decError + '։' : '';
        if (!d) {
            counts.append(state.decError ? '—' : 'Բեռնում եմ որոշումները…');
            $('roDecSum').textContent = '';
            reset.setAttribute('aria-disabled', 'true');
            return;
        }
        const s = d.summary, items = d.decisions;
        const acc = num(s.accepted) || 0, rej = num(s.rejected) || 0, stale = num(s.stale) || 0;
        $('roDecSum').textContent = items.length ? fmt(items.length) : 'դեռ չկան';
        if (!items.length) {
            counts.append('Որոշումներ չկան — ընդունեք կամ թողեք առաջարկները 2-րդ քայլում։');
        } else {
            counts.append('ընդունված է՝ ', h('b', { text: fmt(acc) }), ' · թողնված է ինչպես կա՝ ', h('b', { text: fmt(rej) }));
            if (stale) counts.append(' · հնացած՝ ', h('b', { class: 'is-warn', text: fmt(stale) }));
        }
        reset.setAttribute('aria-disabled', !items.length || state.decBusy ? 'true' : 'false');
        list.hidden = !items.length;
        items.forEach((x, i) => list.append(decRow(x, i)));
    }

    // Решение словами: «օրեր՝ երկ, ամեն շաբաթ → հնգ, 2 շաբաթը մեկ», «որքան հաճախ՝ շաբաթը մեկ անգամ → 2 շաբաթը մեկ»
    function decChange(x) {
        if (x.kind === 'transfer') {
            const v = obj(x.value);
            return { label: 'փոխանցել', from: (x.agent_code ? x.agent_code + '՝ ' : '') + (x.from ? patternHuman(x.from) : humanPlanText(x.from_text)),
                to: (v.agent_code ? v.agent_code + '՝ ' : '') + (Array.isArray(v.pattern) ? patternHuman(v.pattern) : humanPlanText(x.to_text)) };
        }
        if (x.kind === 'remove') return { label: 'հանել', from: x.from ? patternHuman(x.from) : humanPlanText(x.from_text), to: 'չայցելել' };
        if (x.kind === 'freq') return { label: 'որքան հաճախ', from: x.from !== null && x.from !== undefined ? freqHuman(x.from) : humanPlanText(x.from_text),
            to: x.value !== null && x.value !== undefined ? freqHuman(x.value) : humanPlanText(x.to_text) };
        return { label: 'օրեր', from: Array.isArray(x.from) ? patternHuman(x.from) : humanPlanText(x.from_text),
            to: Array.isArray(x.value) ? patternHuman(x.value) : humanPlanText(x.to_text) };
    }

    function decRow(x, i) {
        const word = x.stale ? { cls: 'is-warn', icon: 'fa-triangle-exclamation', text: 'հնացել է' }
            : (x.kind === 'remove' ? (x.status === 'accepted' ? REMOVE_ROW_STATUS.accepted : REMOVE_ROW_STATUS.rejected)
                : (x.kind === 'transfer' ? (x.status === 'accepted' ? TRANSFER_ROW_STATUS.accepted : TRANSFER_ROW_STATUS.rejected)
                    : (x.status === 'accepted' ? ROW_STATUS.accepted : ROW_STATUS.rejected)));
        const name = decName(x), ch = decChange(x);
        return h('li', { class: 'ro-item ro-dec-row' + (x.stale ? ' is-stale' : (x.status === 'rejected' ? ' is-rejected' : ' is-accepted')) },
            h('div', { class: 'ro-item-main' },
                h('div', { class: 'ro-item-name' }, h('span', { class: 'n', text: name }),
                    h('span', { class: 'c', text: [x.customer_code, x.agent_code || x.agent_name].filter(Boolean).map(String).join(' · ') })),
                h('div', { class: 'ro-item-change' }, h('span', { class: 'lbl', text: ch.label + '՝ ' }),
                    h('span', { class: 'from', text: ch.from }), h('span', { class: 'arr', 'aria-hidden': 'true', text: ' → ' }),
                    h('span', { class: 'rt-sr-only', text: ', կլինի՝ ' }), h('span', { class: 'to', text: ch.to })),
                x.stale ? h('div', { class: 'ro-item-note is-warn', text: 'Հիմա ERP-ում՝ ' + humanPlanText(x.current_text) + ' — որոշումը չի կիրառվում։' }) : null,
                x.manager_included === false ? h('div', { class: 'ro-item-note', text: 'Մենեջերը հաշվարկում չէ — ERP-ի համար պլանում չի ներառվի։' }) : null),
            h('div', { class: 'ro-item-side' },
                h('span', { class: 'ro-pill ' + word.cls }, icon(word.icon), word.text),
                h('div', { class: 'ro-item-acts' },
                    h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { i: String(i) },
                        'aria-disabled': state.decBusy ? 'true' : null, 'aria-label': 'Չեղարկել որոշումը՝ ' + name },
                    icon('fa-rotate-left'), 'Չեղարկել'))));
    }

    // Решение сняли не из строки предложения (список, «Сбросить все») — статусы предложений вслед за сервером
    function clearRowDecisions(match) {
        const touched = [];
        allChanges().forEach(c => {
            let changed = false;
            kindsOf(c).forEach(k => {
                if (c.decision[k] && match(c, k)) { c.decision[k] = null; changed = true; }
            });
            if (changed) { refreshRow(c); touched.push(c); }
        });
        if (touched.length) afterDecisions(touched);
    }

    // у «убрать из маршрута» значение одно — совпадает всегда; у «передать» — кому и в какие дни
    const sameValue = (kind, a, b) => (kind === 'remove' ? true
        : (kind === 'pattern' ? patternKey(a) === patternKey(b) : num(a) === num(b)));
    const sameDecision = (c, k, x) => (k === 'transfer'
        ? String(c.to_agent) === String(obj(x.value).agent_id) && patternKey(c.to.pattern) === patternKey(obj(x.value).pattern)
        : sameValue(k, c.to[k === 'pattern' ? 'pattern' : 'freq'], x.value));

    async function resetOne(x, i) {
        if (state.decBusy) return;
        const hadFocus = $('roDecList').contains(document.activeElement);
        state.decBusy = true;
        renderDecisions();
        try {
            // у «передать» значение в списке — с кодом и именем менеджера, серверу — только кому и дни
            const value = x.kind === 'transfer' ? { agent_id: obj(x.value).agent_id, pattern: obj(x.value).pattern } : x.value;
            await api('POST', '/api/routes/decisions', { customer_id: x.customer_id, agent_id: x.agent_id, kind: x.kind, value, action: 'reset' });
            clearRowDecisions((c, k) => k === x.kind && String(c._agent) === String(x.agent_id)
                && String(c.customer_id) === String(x.customer_id) && sameDecision(c, k, x));
            state.lastDecision = Date.now();
            markDirty();
            announce('Որոշումը չեղարկվեց՝ ' + decName(x));
        } catch (e) {
            announce('Չհաջողվեց չեղարկել որոշումը՝ ' + sentence(whyText(e)));
            $('roDecErr').textContent = 'Չհաջողվեց չեղարկել «' + decName(x) + '» խանութի որոշումը՝ ' + whyText(e) + '։';
            $('roDecErr').classList.remove('d-none');
        } finally {
            state.decBusy = false;
            await fetchDecisions();
            if (hadFocus) {   // строка ушла из списка — фокус на соседнюю, иначе на «Сбросить все»
                const btns = $('roDecList').querySelectorAll('button[data-i]');
                const next = btns[Math.min(i, btns.length - 1)];
                (next || $('roDecReset')).focus();
            }
        }
    }

    async function resetAll() {
        const n = state.decisions ? state.decisions.decisions.length : 0;
        if (state.decBusy || !n) return;
        if (!window.confirm('Չեղարկե՞լ բոլոր որոշումները (' + n + ')։\n\nԸնդունված և թողնված առաջարկները կրկին կդառնան «դեռ որոշված չէ», '
            + 'ERP-ի համար պլանում ոչ մի փոփոխություն չի ներառվի։ Այս գործողությունը հետ շրջել հնարավոր չէ։')) return;
        state.decBusy = true;
        renderDecisions();
        try {
            const d = await api('POST', '/api/routes/decisions', { action: 'reset_all' });
            clearRowDecisions(() => true);
            state.lastDecision = Date.now();
            markDirty();
            announce('Բոլոր որոշումները չեղարկվեցին' + (num(d.deleted) !== null ? '՝ ' + fmt(d.deleted) : ''));
        } catch (e) {
            announce('Չհաջողվեց չեղարկել որոշումները՝ ' + sentence(whyText(e)));
            $('roDecErr').textContent = 'Չհաջողվեց չեղարկել որոշումները՝ ' + whyText(e) + '։';
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

    // ---------- Карта (свёрнута; строится при первом раскрытии) ----------
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
    // Длина цикла на карте: «сейчас» — как в плане ERP, «если принять» — цикл расчёта (2 недели)
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
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Առաջարկները և Excel-ը աշխատում են։';
            return;
        }
        const map = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, zoomControl: false });
        L.control.zoom({ zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' }).addTo(map);   // подсказки кнопок — по-армянски (как в настройках)
        RoutesBasemap.add(map);
        map.setView(YEREVAN, 9);
        // колесо мыши масштабирует карту только после клика по ней — страница прокручивается свободно
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.layers = { points: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map), base: L.layerGroup().addTo(map) };
    }
    const mapOpen = () => $('roMapBox').open;

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
        renderTransferToggle();
        renderDays();
        drawMap();
    }

    // «Показать передачи»: стрелки от магазина к дому менеджера, которому его передают (только с передачами)
    function renderTransferToggle() {
        const b = $('roMapTransfers'), n = allChanges().filter(isTransfer).length;
        b.hidden = !(transferMode() && n);
        if (b.hidden) state.mapTransfers = false;
        b.setAttribute('aria-pressed', String(!!state.mapTransfers));
        b.querySelector('span').textContent = state.mapTransfers ? 'Թաքցնել փոխանցումները' : 'Ցույց տալ փոխանցումները';
    }

    const mapTransfersOf = (m) => (m && state.mapTransfers ? allChanges().filter(c => isTransfer(c)
        && (String(c.from_agent) === String(m.agent_id) || String(c.to_agent) === String(m.agent_id))) : []);

    // Направление стрелки на карте: азимут от a к b, градусы (0 — на север)
    const bearing = (a, b) => Math.atan2((b[1] - a[1]) * Math.cos(a[0] * Math.PI / 180), b[0] - a[0]) * 180 / Math.PI;

    function drawTransfers(m, bounds) {
        state.transferMarkers = new Map();
        mapTransfersOf(m).forEach(c => {
            const cust = custOf(c.customer_id) || {};
            if (num(cust.lat) === null || num(cust.lon) === null) return;
            const at = [num(cust.lat), num(cust.lon)];
            const to = mgrOf(c.to_agent), frm = mgrOf(c.from_agent);
            const toHome = to ? homeOf(to) : null, fromHome = frm ? homeOf(frm) : null;
            const color = to && to._color ? to._color : MGR_OTHER;
            if (fromHome) L.polyline([fromHome, at], { color: '#a7b0c0', weight: 2, opacity: 0.7, dashArray: '4 6', interactive: false }).addTo(state.layers.route);
            if (toHome) {
                L.polyline([at, toHome], { color: '#0c0f14', weight: 6, opacity: 0.55, interactive: false }).addTo(state.layers.route);
                L.polyline([at, toHome], { color, weight: 3, opacity: 0.95, interactive: false }).addTo(state.layers.route);
                const mid = [at[0] + (toHome[0] - at[0]) * 0.7, at[1] + (toHome[1] - at[1]) * 0.7];
                L.marker(mid, { icon: L.divIcon({ className: 'rt-arrow', html: h('span', { style: 'color:' + color + ';transform:rotate(' + bearing(at, toHome).toFixed(0) + 'deg)' }, icon('fa-arrow-up')),
                    iconSize: [20, 20], iconAnchor: [10, 10] }), keyboard: false, interactive: false }).addTo(state.layers.route);
                bounds.push(toHome);
            }
            const mk = L.marker(at, { icon: pin('rt-pin-transfer', color, 'fa-people-arrows', 24), title: 'Փոխանցել՝ ' + custName(c.customer_id), zIndexOffset: 800 })
                .bindTooltip(() => h('div', {}, h('b', { text: custName(c.customer_id) }), h('br'),
                    h('span', { style: 'color:#a7b0c0', text: 'փոխանցել՝ ' + agentName(c.from_agent) + ' → ' + agentName(c.to_agent) })), { direction: 'top', offset: [0, -10] })
                .bindPopup(() => popupNode(frm || m, c.customer_id), { maxWidth: 320 })
                .addTo(state.layers.route);
            state.transferMarkers.set(c._key, mk);
            bounds.push(at);
            if (fromHome) bounds.push(fromHome);
        });
    }

    // «На карте» у передачи: карта отдающего менеджера, стрелки передач, подсказка у этого магазина
    function showTransfer(c) {
        state.mapTransfers = true;
        state.mapFocus = c._key;
        renderTransferToggle();
        const m = mgrOf(c._agent);
        if (m) showOnMap(m);
    }

    function onMapToggle() {
        if (!mapOpen() || !state.result) return;
        ensureMap();
        if (state.map) state.map.invalidateSize();
        drawMap();
    }

    function renderDays() {
        const box = $('roDays'), m = curMgr();
        box.textContent = '';
        const days = modeDays(m);
        if (state.mapDay && !days.some(d => dayKey(d) === state.mapDay)) state.mapDay = null;
        if (!days.length) {
            box.append(h('p', { class: 'ro-muted mb-0', text: m ? 'Այս պլանում մենեջերը այցերով օրեր չունի։' : 'Մենեջերներ չկան։' }));
            renderDayInfo();
            return;
        }
        const W = cycleOf(m);
        for (let w = 1; w <= W; w++) {
            const list = days.filter(d => weekOf(d) === w).sort((a, b) => num(a.weekday) - num(b.weekday));
            if (!list.length) continue;
            const grid = h('div', { class: 'ro-daylist', role: 'group', 'aria-label': W > 1 ? ord(w) + ' շաբաթ' : 'Շաբաթվա օրեր' },
                list.map(d => dayButton(d, W)));
            box.append(W > 1 ? h('div', { class: 'ro-weekrow' }, h('span', { class: 'lbl', 'aria-hidden': 'true', text: ord(w) + ' շաբաթ' }), grid) : grid);
        }
        renderDayInfo();
    }

    function dayButton(d, W) {
        const wd = num(d.weekday), w = weekOf(d), visits = num(d.visits) || 0;
        const full = (WD_FULL[wd] || 'օր ' + wd) + (W > 1 ? ', ' + ord(w) + ' շաբաթ' : '');
        return h('button', { type: 'button', class: 'ro-dayb' + (w > 1 ? ' is-w2' : ''), style: '--wd:' + wdColor(wd),
            'aria-pressed': String(state.mapDay === dayKey(d)), dataset: { key: dayKey(d) },
            'aria-label': cap(full) + '՝ ' + fmt(visits) + ' այց, ' + fmt(d.manager_km, 1) + ' կմ' },
            h('span', { class: 'd', text: WD_SHORT[wd] || String(wd) }),
            h('span', { class: 's', text: fmt(visits) + ' այց · ' + fmt(d.manager_km, 0) + ' կմ' }));
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
        const noGeo = d.stops.filter(st => !stopGeo(st)).length;
        box.append(...[
            h('div', { class: 'ro-dayinfo-t', text: cap(WD_FULL[wd] || 'օր ' + wd) + (W > 1 ? ', ' + ord(weekOf(d)) + ' շաբաթ' : '')
                + (state.mapMode === 'before' ? ' — հիմա' : ' — եթե ընդունեք') }),
            h('div', { text: fmt(visits) + ' այց · ' + fmt(d.manager_km, 1) + NB + 'կմ · ամբողջ օրը՝ ' + hm(d.plan_minutes) }),
            num(d.p_day_ge_min) !== null ? h('div', { text: minText() + ' հավաքելու հավանականությունը ձմռանը՝ ' + pct(d.p_day_ge_min) }) : null,
            noGeo ? h('div', { class: 'ro-warnline' }, icon('fa-location-crosshairs'),
                fmt(noGeo) + ' խանութ առանց կոորդինատների — քարտեզում դրանք չկան') : null,
            homeOf(m) ? null : h('div', { class: 'ro-warnline' }, icon('fa-house'), 'տունը հայտնի չէ — գիծը՝ առաջին խանութից մինչև վերջինը'),
            h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm mt-2', dataset: { all: '1' } }, icon('fa-xmark'), 'Ցույց տալ բոլոր օրերը'),
        ].filter(Boolean));   // append(null) вставил бы текст «null»
    }

    function updateMapNote(m) {
        const note = $('roMapNote');
        if (!m) { note.textContent = ''; return; }
        const ids = new Set(), noGeo = new Set();
        modeDays(m).forEach(d => d.stops.forEach(st => { (stopGeo(st) ? ids : noGeo).add(String(st.customer_id)); }));
        noGeo.forEach(id => { if (ids.has(id)) noGeo.delete(id); });
        note.textContent = 'քարտեզում՝ ' + fmt(ids.size) + ' խանութ'
            + (noGeo.size ? ' · առանց կոորդինատների՝ ' + fmt(noGeo.size) : '');
    }

    function renderMapLegend(m) {
        const box = $('roMapLegend');
        box.textContent = '';
        if (!m) return;
        const W = cycleOf(m);
        const wds = [...new Set(modeDays(m).map(d => num(d.weekday)))].sort((a, b) => a - b);
        const item = (sw, text) => h('span', { class: 'rt-lg rt-lg-static' }, sw, text);
        box.append(h('span', { class: 'rt-lg-cap', text: 'Կետի գույնը ցույց է տալիս շաբաթվա օրը՝' }));
        wds.forEach(wd => box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:' + wdColor(wd), 'aria-hidden': 'true' }),
            WD_SHORT[wd] || String(wd))));
        if (W > 1) {
            box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:#a7b0c0', 'aria-hidden': 'true' }), 'ամեն շաբաթ կամ 1-ին շաբաթը'));
            box.append(item(h('span', { class: 'ro-lg-sw is-hollow', style: 'color:#a7b0c0', 'aria-hidden': 'true' }), 'միայն 2-րդ շաբաթը — կետագիծ'));
        }
        box.append(item(h('span', { class: 'rt-lg-ico', style: 'border:1.5px solid #a7b0c0;border-radius:50%', 'aria-hidden': 'true' }, icon('fa-house')), 'մենեջերի տուն'));
        if (depotOf()) box.append(item(h('span', { class: 'rt-lg-ico', style: 'background:#eef1f6;color:#0c0f14', 'aria-hidden': 'true' }, icon('fa-warehouse')), 'պահեստ'));
        if (mapTransfersOf(m).length) box.append(item(h('span', { class: 'rt-lg-ico', style: 'border:1.5px solid #a7b0c0;border-radius:50%', 'aria-hidden': 'true' },
            icon('fa-people-arrows')), 'փոխանցել — սլաք դեպի նրա տունը, ում փոխանցում են'));
    }

    // Дни магазина в плане режима словами: «երկ և հնգ, ամեն շաբաթ»
    function visitsHuman(m, side, cid) {
        const days = side === 'before' ? m.days_before : m.days_after;
        const pairs = [];
        days.forEach(d => { if (d.stops.some(st => String(st.customer_id) === String(cid))) pairs.push([weekOf(d), num(d.weekday)]); });
        const W = Math.max(side === 'after' ? num(state.result.cycle_weeks) || 1 : 1, ...days.map(weekOf));
        if (W === 1) return patternHuman(pairs.flatMap(([, d]) => [[1, d], [2, d]]));   // недельный план — обе недели
        if (W > 2) return daysList(uniqSorted(pairs.map(x => x[1])));
        return patternHuman(pairs);
    }

    function tipNode(m, cid) {
        return h('div', {}, h('b', { text: custName(cid) }), h('br'),
            h('span', { style: 'color:#a7b0c0', text: visitsHuman(m, state.mapMode, cid) }));
    }

    function popupNode(m, cid) {
        const c = custOf(cid) || {};
        const ch = m.changes.find(x => String(x.customer_id) === String(cid)) || null;
        const row = (k, v) => h('div', { class: 'rt-pop-row' }, h('span', { text: k }), h('b', { text: v }));
        const silent = silenceText(cid);
        const rate = orderRateText(c.lam_year);
        const word = ch ? (isRemove(ch) ? REMOVE_WORD : (isTransfer(ch) ? TRANSFER_WORD : DECISION_WORD))[statusOf(ch)] || DECISION_WORD.none : 'փոփոխություններ չկան';
        return h('div', { class: 'rt-pop' },
            h('div', { class: 'rt-pop-t', text: custName(cid) }),
            c.code ? h('div', { class: 'rt-pop-s', text: String(c.code) }) : null,
            row('Հիմա', ch ? patternHuman(ch.from.pattern) : visitsHuman(m, 'before', cid)),
            row('Եթե ընդունեք', ch ? (isRemove(ch) ? 'չայցելել' : patternHuman(ch.to.pattern)) : visitsHuman(m, 'after', cid)),
            ch ? h('div', { class: 'rt-pop-note', text: rowReason(ch) }) : null,
            silent ? row('Գնումներ', silent) : (rate ? row('Պատվերներ', rate) : null),
            row('Որոշում', word));
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
        if (!state.map || !mapOpen()) return;
        const layers = state.layers;
        layers.points.clearLayers();
        layers.route.clearLayers();
        layers.base.clearLayers();
        const bounds = [];
        if (m) {
            const days = modeDays(m), W = cycleOf(m), sel = selectedDay(m);
            // Точка — магазин × день недели; только 2-я неделя — пунктиром и полупрозрачно
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
                L.marker(home, { icon: pin('rt-pin-home', m._color, 'fa-house', 24), title: 'Տուն՝ ' + mgrName(m), zIndexOffset: 500 })
                    .bindTooltip(() => h('div', {}, h('b', { text: 'Տուն' }), ' · ' + mgrName(m)), { direction: 'top', offset: [0, -10] })
                    .addTo(layers.base);
                bounds.push(home);
            }
        }
        drawTransfers(m, bounds);
        const dp = depotOf();
        if (dp) {
            L.marker(dp, { icon: pin('rt-pin-depot', null, 'fa-warehouse', 28), title: 'Պահեստ', zIndexOffset: 1000 })
                .bindTooltip(() => h('div', {}, h('b', { text: 'Պահեստ' }), ' — այստեղից են մեկնում մեքենաները'), { direction: 'top', offset: [0, -12] })
                .addTo(layers.base);
        }
        state.map.invalidateSize();
        if (bounds.length) state.map.fitBounds(L.latLngBounds(bounds).pad(0.08), { animate: !RM, maxZoom: 15 });
        else state.map.setView(YEREVAN, 9, { animate: !RM });
        const focus = state.mapFocus && state.transferMarkers ? state.transferMarkers.get(state.mapFocus) : null;
        state.mapFocus = null;
        if (focus) focus.openPopup();
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
        const box = $('roMapBox');
        renderDays();
        if (!box.open) box.open = true;   // toggle → onMapToggle нарисует карту
        else drawMap();
        scrollTo(box);
        announce('Քարտեզում՝ ' + mgrName(m));
    }

    // ---------- План для ERP (Excel) ----------
    const acceptedChanges = () => (state.result ? state.result.managers.flatMap(m => m.changes
        .filter(c => kindsOf(c).some(k => c.decision[k] === 'accepted')).map(c => ({ c, m }))) : []);

    // Сколько изменений войдёт в файл — считает сервер (как plan-export): принятые и у менеджеров вне
    // этого расчёта, без устаревших и уже исполненных в ERP
    function renderExport() {
        const s = state.decisions ? state.decisions.summary : null;
        const n = s ? num(s.export_changes) : null;
        const btn = $('roExportBtn'), note = $('roExportNote');
        btn.setAttribute('aria-disabled', n === 0 || state.exporting ? 'true' : 'false');
        let status = '';
        if (n === null) {
            status = state.decError ? 'Չհաջողվեց պարզել, թե քանի փոփոխություն կմտնի ֆայլ — այն միևնույն է կարելի է ներբեռնել։' : '';
        } else if (n) {
            status = 'Ֆայլում կլինի ' + fmt(n) + ' ընդունված փոփոխություն։';
        } else {
            status = 'Դեռ ներբեռնելու բան չկա՝ ընդունեք գոնե մեկ առաջարկ։';
        }
        note.textContent = '';
        note.append('Ֆայլում նոր պլանն է՝ ըստ մենեջերների և օրերի։ Այն ձեռքով մուտքագրեք ERP-ում։ ',
            status ? h('span', { class: n === 0 ? 'is-warn' : 'is-ok', text: status }) : null);
    }

    function setExportBusy(on) {
        state.exporting = on;
        const btn = $('roExportBtn');
        btn.setAttribute('aria-busy', on ? 'true' : 'false');
        btn.querySelector('i').className = on ? 'rt-spin-inline' : 'fas fa-file-excel';
        btn.querySelector('span').textContent = on ? 'Պատրաստում եմ ֆայլը…' : 'Ներբեռնել պլանը ERP-ի համար (Excel)';
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
        const MARK = { move: 'տեղափոխում', frequency: 'հաճախականություն', freq: 'հաճախականություն', both: 'տեղափոխում, հաճախականություն', remove: 'հանել', transfer: 'փոխանցել' };
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
        return { rows, head: addr ? PLAN_HEAD.concat('Առաքման հասցե (ID)') : PLAN_HEAD, cols: addr ? PLAN_COLS.concat(18) : PLAN_COLS };
    }

    const patternKey = (p) => JSON.stringify(arr(p).filter(Array.isArray).map(x => [num(x[0]), num(x[1])])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1]));

    // Текст шаблона для Excel: с сервера, иначе — из пар [неделя, день]: «երք, ամեն շաբաթ» (как patterns.pattern_text)
    function patternText(pairs, W) {
        const by = new Map();
        pairs.forEach(([w, wd]) => {
            if (!by.has(wd)) by.set(wd, new Set());
            by.get(wd).add(w);
        });
        if (!by.size) return 'առանց այցերի';
        const wds = [...by.keys()].sort((a, b) => a - b);
        const names = wds.map(wd => (WD_SHORT[wd] || String(wd)).toLowerCase());
        const sets = wds.map(wd => [...by.get(wd)].sort((a, b) => a - b).join(','));
        const every = Array.from({ length: W }, (_, i) => i + 1).join(',');
        if (sets.every(s => s === every)) return names.join(' և ') + ', ամեն շաբաթ';
        if (sets.every(s => s === sets[0]) && !sets[0].includes(',')) return names.join(' և ') + ', ' + W + ' շաբաթից ' + sets[0] + (sets[0] === '1' ? '-ինը' : '-րդը');
        return wds.map((wd, i) => names[i] + ' (շաբաթ ' + sets[i].replace(/,/g, ' և ') + ')').join(', ');
    }
    function sideText(side) {
        if (typeof side.text === 'string' && side.text) return side.text;
        const pairs = arr(side.pattern).filter(Array.isArray).map(x => [num(x[0]) || 1, num(x[1])]).filter(x => x[1] !== null);
        return patternText(pairs, Math.max(2, ...pairs.map(x => x[0])));
    }

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
                type === 'transfer' && pick(x, 'from_agent_code') && pick(x, 'to_agent_code')
                    ? 'փոխանցել՝ ' + pick(x, 'from_agent_code') + ' → ' + pick(x, 'to_agent_code') : (TYPE_TEXT[type] || markText(type)),
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
            showExportError('Excel-ի գրադարանը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Թարմացրեք էջը։');
            return;
        }
        setExportBusy(true);
        try {
            const d = await api('GET', '/api/routes/plan-export');
            renderExportWarn(d);
            const plan = planSheet(d), changes = changeRows(d);
            if (!plan.rows.length) throw new Error('Սերվերը դատարկ պլան վերադարձրեց։');
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, sheet(plan.head, plan.rows, plan.cols), 'Պլան');
            XLSX.utils.book_append_sheet(wb, sheet(CHANGE_HEAD, changes, CHANGE_COLS), 'Փոփոխություններ');
            const file = 'plan_marshrutov_' + ymd(new Date()) + '.xlsx';
            XLSX.writeFile(wb, file);
            announce('Ֆայլը ներբեռնված է՝ ' + file + ' (պլանի տող՝ ' + plan.rows.length + ', փոփոխություն՝ ' + changes.length + ')');
        } catch (e) {
            showExportError('Ֆայլը չստացվեց՝ ' + (e.network ? 'սերվերի հետ կապ չկա։ Ստուգեք ցանցը և սեղմեք նորից։' : sentence(e.message)));
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
                x.agent_code ? ' (' + x.agent_code + ')' : '', '՝ որոշված է «' + humanPlanText(x.to_text) + '», հիմա ERP-ում՝ «'
                + humanPlanText(x.current_text) + '»')));
            if (stale.length > STALE_LIST_MAX) ul.append(h('li', { text: 'և ևս ' + fmt(stale.length - STALE_LIST_MAX) }));
            lines.push(h('span', {}, h('b', { text: 'Չեն ներառվել ' + fmt(stale.length) + ' հնացած որոշում' }),
                ' — այս խանութների պլանը ERP-ում փոխվել է որոշումից հետո։', ul));
        }
        const mm = dataMismatch(d.data_as_of);
        if (mm) lines.push(h('span', {}, 'ERP-ի համար պլանը կազմված է ERP-ի ' + stamp(mm.now) + ' դրությամբ տվյալներով, իսկ էկրանի հաշվարկը՝ '
            + stamp(mm.calc) + ' դրությամբ։ Ընդունված փոփոխությունները կիրառված են ERP-ի ընթացիկ պլանին։ Որպեսզի առաջարկները համընկնեն դրա հետ, վերահաշվեք։'));
        box.classList.toggle('d-none', !lines.length);
        if (lines.length) box.append(icon('fa-triangle-exclamation'), h('span', { class: 'rt-alert-text' }, lines.map(x => h('span', { class: 'ro-sub-line' }, x))));
    }

    // ---------- Справочники: менеджеры, дома, склад, сезоны, порог дня ----------
    async function loadRefs() {
        $('roMgrsErr').classList.add('d-none');
        const [ov, st] = await Promise.allSettled([api('GET', '/api/routes/overview'), api('GET', '/api/routes/settings')]);
        if (st.status === 'fulfilled' && isObj(st.value.settings)) state.settings = st.value.settings;
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
            state.season = obj(d.season);
            state.roads = d.distance_source === 'roads';
            state.pick = new Set(state.managers.filter(m => m.included).map(m => String(m.agent_id)));
            renderPicker();
        } else {
            const e = ov.reason || {};
            const box = $('roMgrs');
            box.textContent = '';
            box.append(h('span', { class: 'ro-muted', text: 'Մենեջերների ցուցակը չբեռնվեց — հաշվարկը կկատարվի կարգավորումներում «հաշվարկում» նշված մենեջերներով։' }));
            const err = $('roMgrsErr');
            err.textContent = '';
            const again = h('button', { type: 'button', class: 'rt-linkbtn', text: 'Կրկին բեռնել' });
            again.addEventListener('click', () => {
                box.textContent = '';
                box.append(h('span', { class: 'ro-muted' }, spin(), ' Բեռնում եմ մենեջերների ցուցակը…'));
                loadRefs();
            });
            err.append(h('span', { text: e.network ? 'Սերվերի հետ կապ չկա։' : sentence(e.message || 'Սերվերի սխալ։') }), again);
            err.classList.remove('d-none');
            updateRunState();
        }
        // тексты зависят от порога дня, рабочих дней, сезонов и склада — перерисовать с ними
        if (state.result) {
            renderTexts();
            renderDayInfo();
            drawMap();
        }
    }

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        syncNavOffset();
        window.addEventListener('resize', syncNavOffset);
        initTips();
        $('roMgrs').parentElement.querySelector('.ro-quick').hidden = true;
        renderStep1();
        renderStep3();
        updateRunState();

        $('roForm').addEventListener('submit', (e) => {
            e.preventDefault();
            if ($('roRunBtn').getAttribute('aria-disabled') === 'true' && !running()) {
                announce('Նշեք գոնե մեկ մենեջեր');
                $('roMore').open = true;
                return;
            }
            run(readParams());
        });
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
            if ($('roRecalcBtn').getAttribute('aria-disabled') === 'true') { announce('Հաշվարկն արդեն ընթանում է — սպասեք ավարտին'); return; }
            recalc();
        });
        $('roParamsToggle').addEventListener('click', () => {
            state.paramsOpen = !state.paramsOpen;
            renderStep1();
            if (state.paramsOpen) {
                if (state.result) setParams(state.result.params);
                $('roMore').open = true;
                $('roRunBtn').focus();
            }
        });

        $('roCards').addEventListener('click', onCardsClick);
        $('roPanel').addEventListener('click', onPanelClick);
        $('roPanel').addEventListener('input', debounce(onPanelSearch, 150));

        $('roMapBox').addEventListener('toggle', onMapToggle);
        $('roMapTransfers').addEventListener('click', () => {
            state.mapTransfers = !state.mapTransfers;
            renderTransferToggle();
            drawMap();
            announce(state.mapTransfers ? 'Քարտեզում՝ խանութների փոխանցումները' : 'Փոխանցումները թաքցված են');
        });
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
        $('roStepWarn').addEventListener('click', (e) => {
            if (!e.target.closest('button[data-showdec]')) return;
            const box = $('roDecBox');
            box.open = true;
            scrollTo(box);
            box.querySelector('summary').focus({ preventScroll: true });
        });
        $('roDecList').addEventListener('click', onDecList);
        $('roDecReset').addEventListener('click', () => {
            if ($('roDecReset').getAttribute('aria-disabled') === 'true') { announce('Որոշումներ չկան — չեղարկելու բան չկա'); return; }
            resetAll();
        });

        loadRefs();
        loadLast(false);
        resumeJob();
    });
})();

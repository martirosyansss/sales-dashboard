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

    const WD_SHORT = { 1: 'Пн', 2: 'Вт', 3: 'Ср', 4: 'Чт', 5: 'Пт', 6: 'Сб', 7: 'Вс' };
    const WD_LOWER = { 1: 'пн', 2: 'вт', 3: 'ср', 4: 'чт', 5: 'пт', 6: 'сб', 7: 'вс' };
    const WD_FULL = { 1: 'понедельник', 2: 'вторник', 3: 'среда', 4: 'четверг', 5: 'пятница', 6: 'суббота', 7: 'воскресенье' };
    const WD_FROM = { 1: 'понедельника', 2: 'вторника', 3: 'среды', 4: 'четверга', 5: 'пятницы', 6: 'субботы', 7: 'воскресенья' };
    const MONTHS_FULL = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
    // Цвет точки на карте — день недели. Оттенки из палитры менеджеров обзора (контраст к тёмной карте ≥ 3:1)
    const WD_COLORS = { 1: '#18c1fc', 2: '#fe904d', 3: '#b0a2ff', 4: '#b8b90c', 5: '#14cfa3', 6: '#fe80c0', 7: '#8b93a7' };
    // Цвета менеджеров — как в обзоре: слот по позиции менеджера в ответе
    const MGR_COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3',
                        '#0d9298', '#b8b90c', '#ae55c1', '#37981b', '#fe80c0', '#d64651'];
    const MGR_OTHER = '#8b93a7';
    // Вид изменения — для листа «Изменения» в Excel (как было)
    const TYPE_TEXT = { move: 'перенос', frequency: 'частота', both: 'перенос и частота', remove: 'убрать', transfer: 'передать' };
    // Какие решения отправляет изменение: у «both» — два, шаблон дней и частота; «убрать из маршрута» — remove;
    // «передать другому менеджеру» (этап 4) — transfer: кому и в какие дни
    const KINDS = { move: ['pattern'], frequency: ['freq'], both: ['pattern', 'freq'], remove: ['remove'], transfer: ['transfer'] };
    const ACTION_STATUS = { accept: 'accepted', reject: 'rejected', reset: null };
    // Состояние строки предложения — подпись и значок (без решения — только кнопки)
    const ROW_STATUS = {
        accepted: { cls: 'is-ok', icon: 'fa-check', text: 'Принято' },
        rejected: { cls: 'is-off', icon: 'fa-minus', text: 'Оставлено как есть' },
        mixed: { cls: 'is-warn', icon: 'fa-circle-half-stroke', text: 'Принято частично' },
    };
    // «Убрать из маршрута» (§15): принять — убрать, отклонить — оставить
    const REMOVE_ROW_STATUS = {
        accepted: { cls: 'is-bad', icon: 'fa-user-minus', text: 'Будет убран из маршрута' },
        rejected: { cls: 'is-ok', icon: 'fa-user-check', text: 'Остаётся в маршруте' },
    };
    // «Передать другому менеджеру»: принять — передать, отклонить — оставить у своего
    const TRANSFER_ROW_STATUS = {
        accepted: { cls: 'is-ok', icon: 'fa-people-arrows', text: 'Будет передан' },
        rejected: { cls: 'is-off', icon: 'fa-minus', text: 'Остаётся у своего менеджера' },
    };
    const DECISION_WORD = { none: 'пока не решено', accepted: 'принято', rejected: 'оставлено как есть', mixed: 'принято частично' };
    const REMOVE_WORD = { none: 'пока не решено', accepted: 'убрать из маршрута', rejected: 'оставить в маршруте', mixed: 'пока не решено' };
    const TRANSFER_WORD = { none: 'пока не решено', accepted: 'передать', rejected: 'оставить у своего менеджера', mixed: 'пока не решено' };
    // Затих, потерян, ни одного заказа за год (§15)
    const SILENT = new Set(['dormant', 'lost', 'never']);
    // Группы предложений по смыслу (бриф): порядок, значок, короткая подпись переключателя;
    // incoming — магазины, которые этому менеджеру передают другие (решение то же, что у отдающего)
    const GROUP_ORDER = ['transfer', 'incoming', 'freq', 'move', 'offday', 'winback', 'remove', 'other'];
    const GROUP_ICON = { transfer: 'fa-people-arrows', incoming: 'fa-people-arrows', freq: 'fa-calendar-minus', move: 'fa-shuffle',
                         offday: 'fa-calendar-xmark', winback: 'fa-hand-holding-heart', remove: 'fa-user-minus', other: 'fa-pen', hints: 'fa-lightbulb' };
    // Решения только по одному магазину, с подтверждением: «Принять все в группе» у этих групп нет
    const ONE_BY_ONE = new Set(['remove', 'transfer', 'incoming']);
    const RUN_NOTE_TAIL = 'ERP только читается';
    const PLAN_HEAD = ['Код менеджера', 'Менеджер', 'Неделя цикла', 'День', '№', 'Код клиента', 'Клиент', 'Отметка'];
    const PLAN_COLS = [14, 28, 13, 7, 6, 14, 42, 18];
    const CHANGE_HEAD = ['Код менеджера', 'Менеджер', 'Код клиента', 'Клиент', 'Что меняется', 'Было', 'Стало', 'Причина',
                         'Км менеджера в неделю', 'Км грузовика в неделю', 'Слабых дней в неделю', 'Минут в неделю'];
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
        if (a >= 1e6) return fmt(n / 1e6, 1) + NB + 'млн';
        if (a >= 1e3) return fmt(Math.round(n / 1e3)) + NB + 'тыс.';
        return fmt(Math.round(n));
    }
    // «≈ 22 000» — сумма округлена так, чтобы было понятно порядок, а не копейки
    function moneyRound(v) {
        const a = Math.abs(v), step = a >= 10000 ? 1000 : (a >= 1000 ? 100 : 10);
        return fmt(Math.round(v / step) * step);
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
        return m ? m + NB + 'мин' + (r ? ' ' + r + NB + 'с' : '') : r + NB + plural(r, 'секунду', 'секунды', 'секунд');
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
    // «30.09 в 23:21»
    const dayTime = (t) => t.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' в '
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
                'aria-label': 'Пояснение' + (label ? ': ' + label : '') }, '?'),
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

    // ---------- Слова вместо жаргона (словарь брифа) ----------
    const minDay = () => num(state.settings && state.settings.min_day_revenue);
    // «100 000 драм» — порог дня; без настроек — «дневной нормы»
    const minText = () => (minDay() !== null ? fmt(minDay()) + NB + 'драм' : 'дневной нормы');
    const workdays = () => {
        const w = state.settings && Array.isArray(state.settings.workdays) ? state.settings.workdays.map(Number) : null;
        return new Set(w && w.length ? w : [1, 2, 3, 4, 5, 6]);
    };
    // «пн», «пн и чт», «пн, ср и пт»
    function daysList(days) {
        const names = days.map(d => WD_LOWER[d] || String(d));
        return names.length <= 1 ? (names[0] || '') : names.slice(0, -1).join(', ') + ' и ' + names[names.length - 1];
    }
    const uniqSorted = (list) => [...new Set(list)].sort((a, b) => a - b);
    const pairsOf = (p) => arr(p).filter(Array.isArray).map(x => [num(x[0]) || 1, num(x[1])]).filter(x => x[1] !== null);
    // Визиты словами: «вт каждую неделю», «чт раз в 2 недели», «пн и чт каждую неделю»; пусто — «не посещать»
    function patternHuman(p) {
        const pairs = pairsOf(p);
        if (!pairs.length) return 'не посещать';
        const w1 = uniqSorted(pairs.filter(x => x[0] === 1).map(x => x[1]));
        const w2 = uniqSorted(pairs.filter(x => x[0] === 2).map(x => x[1]));
        if (w1.join() === w2.join()) return daysList(w1) + ' каждую неделю';
        if (!w2.length) return daysList(w1) + ' раз в 2 недели';
        if (!w1.length) return daysList(w2) + ' раз в 2 недели';
        return daysList(w1) + ' в 1-ю неделю, ' + daysList(w2) + ' во 2-ю';
    }
    // Визит «раз в 2 недели» — в какую неделю двухнедельного цикла (так и в Excel)
    function cycleWeekNote(p) {
        const weeks = uniqSorted(pairsOf(p).map(x => x[0]));
        if (weeks.length !== 1) return null;
        return 'Визит — в ' + (weeks[0] === 1 ? '1-ю' : '2-ю') + ' неделю из двух.';
    }
    function freqHuman(f) {
        const n = num(f);
        if (n === null) return '—';
        if (n <= 0) return 'не посещать';
        if (Math.abs(n - 0.5) < 1e-9) return 'раз в 2 недели';
        if (Math.abs(n - 1) < 1e-9) return 'раз в неделю';
        return fmt(n, 1) + ' ' + (Number.isInteger(n) ? plural(n, 'раз', 'раза', 'раз') : 'раза') + ' в неделю';
    }
    // Текст шаблона с сервера («пн, 1-я неделя из 2») — теми же словами, что и на странице
    const humanPlanText = (t) => String(t || '—').replace(/, каждую неделю/g, ' каждую неделю')
        .replace(/, 1-я неделя из 2/g, ' раз в 2 недели').replace(/, 2-я неделя из 2/g, ' раз в 2 недели')
        .replace(/клиента нет в плане/g, 'магазина нет в плане');
    // Как часто магазин заказывает: «примерно раз в 3 недели», «почти каждую неделю», «2 раза в неделю»
    function orderRateText(lam) {
        const l = num(lam);
        if (l === null) return null;
        if (l <= 0) return 'за год ни одного заказа';
        if (l >= 0.95) return 'заказывает ' + fmt(round(l, 1), 1) + ' ' + (Number.isInteger(round(l, 1)) ? plural(round(l, 1), 'раз', 'раза', 'раз') : 'раза') + ' в неделю';
        const w = Math.round(1 / l);
        return w <= 1 ? 'заказывает почти каждую неделю' : 'заказывает примерно раз в ' + w + ' ' + plural(w, 'неделю', 'недели', 'недель');
    }
    function monthsAgo(days) {
        const m = Math.floor(days / 30.4);
        if (m >= 12) return 'больше года';
        return m <= 1 ? 'больше месяца' : 'больше ' + m + ' месяцев';
    }
    // Статус покупок (§15) словами: «перестал покупать 79 дней назад», «не покупает больше 9 месяцев»
    function silenceText(id) {
        const c = custOf(id) || {}, d = num(c.silent_days);
        if (c.status === 'never') return 'ни одного заказа за год';
        if (c.status === 'lost') return d !== null ? 'не покупает ' + monthsAgo(d) : 'давно не покупает';
        if (c.status === 'dormant') return d !== null ? 'перестал покупать ' + fmt(d) + NB + plural(d, 'день', 'дня', 'дней') + ' назад' : 'перестал покупать';
        return null;
    }
    const lostMonths = () => {
        const d = num(state.settings && state.settings.lost_min_days);
        const m = Math.max(1, Math.round((d !== null ? d : 120) / 30));
        return m === 1 ? 'месяца' : m + ' ' + (m % 10 === 1 && m % 100 !== 11 ? 'месяца' : 'месяцев');
    };
    function durText(mins) {
        const m = Math.round(Math.abs(mins));
        if (m < 60) return m + NB + 'мин';
        const hh = Math.floor(m / 60), r = m % 60;
        return hh + NB + 'ч' + (r ? ' ' + r + NB + 'мин' : '');
    }
    // Разница в длине дня словами: «почти на час короче», «на 20 минут длиннее»; меньше 5 минут — null
    function dayShift(minutes) {
        const m = Math.abs(minutes), word = minutes > 0 ? 'короче' : 'длиннее';
        if (m < 5) return null;
        if (m < 40) return 'на ' + Math.max(5, Math.round(m / 5) * 5) + ' минут ' + word;
        if (m < 58) return 'почти на час ' + word;
        if (m < 70) return 'на час ' + word;
        const hh = Math.round(m / 30) / 2;
        return 'на ' + fmt(hh, 1) + ' ' + (Number.isInteger(hh) ? plural(hh, 'час', 'часа', 'часов') : 'часа') + ' ' + word;
    }

    // ---------- Шаг 1 · Посчитать ----------
    const mgrName = (m) => (m && m.name ? String(m.name) : 'Менеджер ' + (m ? (m.code || m.agent_id) : ''));

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
        $('roMgrCount').textContent = n ? 'отмечено ' + state.pick.size + ' из ' + n + (off ? ' · не в расчёте ' + off : '') : '';
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
    // «≈ 5 секунд» — по длительности последнего расчёта, с запасом до 5 секунд
    function runNote() {
        const s = state.result ? num(state.result.seconds) : null;
        const sec = Math.max(5, Math.ceil((s || 0) / 5) * 5);
        return sec < 60 ? '≈' + NB + sec + NB + 'секунд, ' + RUN_NOTE_TAIL
            : '≈' + NB + Math.ceil(sec / 60) + NB + 'мин, ' + RUN_NOTE_TAIL;
    }

    function updateRunState() {
        const busy = running();
        const none = !!state.managers && state.pick.size === 0;
        const btn = $('roRunBtn');
        btn.setAttribute('aria-disabled', busy || none ? 'true' : 'false');
        btn.setAttribute('aria-busy', busy ? 'true' : 'false');
        btn.querySelector('i').className = busy ? 'rt-spin-inline' : 'fas fa-play';
        btn.querySelector('span').textContent = busy ? 'Считаю…' : 'Посчитать предложения';
        const note = $('roRunNote');
        note.textContent = busy ? 'Можно уйти со страницы — расчёт продолжится на сервере'
            : (none ? 'Отметьте хотя бы одного менеджера в «Дополнительно»' : runNote());
        note.classList.toggle('is-warn', none && !busy);
        ['roMgrFs', 'roModeFs', 'roStartFs', 'roFreqFs'].forEach(id => { $(id).disabled = busy; });
        const rb = $('roRecalcBtn');
        rb.setAttribute('aria-disabled', busy ? 'true' : 'false');
        rb.setAttribute('aria-busy', busy ? 'true' : 'false');
        rb.querySelector('i').className = busy ? 'rt-spin-inline' : 'fas fa-rotate';
        rb.querySelector('span').textContent = busy ? 'Считаю…' : (state.dirty ? 'Пересчитать с учётом решений' : 'Пересчитать');
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
        tg.textContent = state.paramsOpen ? 'Скрыть параметры' : 'Изменить параметры';
        if (!has) return;
        const t = parseTime(r.generated_at), n = r.managers.length, p = r.params;
        const parts = [(t ? 'Посчитано ' + dayTime(t) : 'Последний расчёт') + ' по ' + n + ' ' + plural(n, 'менеджеру', 'менеджерам', 'менеджерам')];
        if (p.mode === 'transfer') parts.push('с передачей магазинов между менеджерами');
        if (p.start === 'fresh') parts.push('магазины разложены заново');
        if (p.frequencies === 'current') parts.push('частота — как сейчас');
        const el = $('roDoneText');
        el.textContent = '';
        el.append(icon('fa-circle-check'), h('span', { text: parts.join(', ') }));
        const snap = parseTime(r.snapshot_as_of);
        if (snap) el.append(tip('Данные ERP прочитаны ' + dayTime(snap) + (num(r.seconds) !== null ? ', расчёт занял ' + duration(r.seconds) : '')
            + '. «Пересчитать» — посчитать заново с теми же менеджерами и с учётом ваших решений.', 'когда посчитано'));
    }

    // ---------- Запуск и опрос задачи ----------
    function run(params) {
        if (running()) { announce('Расчёт уже идёт — дождитесь окончания'); return; }
        if (Array.isArray(params.agent_ids) && !params.agent_ids.length) {
            announce('Отметьте хотя бы одного менеджера');
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
        showRunning(resumed ? 'Проверяю ход расчёта…' : 'Читаю данные из ERP…');
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

    // «Считаю менеджера 4 из 9 — Имя»: done — сколько менеджеров уже посчитано, agent_code — тот, что считается сейчас
    function showProgress(p) {
        const done = num(p.done), total = num(p.total);
        const code = p.agent_code === null || p.agent_code === undefined ? '' : String(p.agent_code);
        let text;
        if (total === null || total <= 0) {
            text = 'Читаю данные из ERP…';
        } else if (done !== null && done >= total) {
            // режим с передачами: после дней по менеджерам — поиск передач (дольше остального)
            text = state.lastRun && state.lastRun.mode === 'transfer'
                ? 'Ищу, каких магазинов выгодно передать другим менеджерам…' : 'Сравниваю, что было и что станет…';
        } else {
            const k = Math.min(total, Math.max(0, done || 0) + 1);
            const who = typeof p.agent_name === 'string' && p.agent_name ? p.agent_name
                : ((code && state.managers && state.managers.find(m => String(m.code) === code)) || {}).name;
            text = 'Считаю менеджера ' + k + ' из ' + total + (who ? ' — ' + who : (code ? ' — ' + code : ''));
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

    // «Пересчитать»: те же менеджеры и параметры, что у показанного результата; решения сервер учтёт сам
    function recalc() {
        if (running()) { announce('Расчёт уже идёт — дождитесь окончания'); return; }
        if (allChanges().some(c => c._saving)) { announce('Дождитесь, пока сохранятся решения'); return; }
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
            announce('Менеджеры этого расчёта больше не в расчёте — отметьте менеджеров');
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

    // Готовый результат новой задачи: фокус — на шаг 2. Решения до запуска в него вошли; сохранённые
    // уже во время расчёта (startedAt — начало задачи) — нет, и «пересчитайте» остаётся
    function acceptResult(res, focus, startedAt) {
        const stale = !!startedAt && state.lastDecision > startedAt;
        clearDirty();
        state.paramsOpen = false;
        setResult(res);
        if (stale) markDirty();
        const n = allChanges().length;
        announce('Расчёт готов: ' + (n ? n + ' ' + plural(n, 'предложение', 'предложения', 'предложений') : 'изменений нет'));
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
    const custName = (id) => { const c = custOf(id); return c && c.name ? String(c.name) : 'Магазин ' + id; };
    const custCode = (id) => { const c = custOf(id); return c && c.code ? String(c.code) : ''; };
    const allChanges = () => (state.result ? state.result.managers.flatMap(m => m.changes) : []);
    const kindsOf = (c) => KINDS[c.type] || KINDS.move;
    const isRemove = (c) => c.type === 'remove';
    const isTransfer = (c) => c.type === 'transfer';
    const transferMode = () => !!state.result && state.result.params.mode === 'transfer';
    // Имя менеджера по id — из результата (все стороны передачи в расчёте), иначе из списка менеджеров
    const agentName = (id) => { const m = id === null || id === undefined ? null : mgrOf(id); return m ? mgrName(m) : 'другой менеджер'; };
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

    // Группа предложения по смыслу: убрать → вернуть (перестал покупать) → с нерабочего дня → реже → другой день
    function groupOf(c) {
        if (isTransfer(c)) return 'transfer';
        if (isRemove(c)) return 'remove';
        if (isSilent(c.customer_id)) return 'winback';
        if (offDaysOf(c.from.pattern).length) return 'offday';
        const ff = num(c.from.freq), tf = num(c.to.freq);
        if (ff !== null && tf !== null && tf < ff - 1e-9) return 'freq';
        if (ff !== null && tf !== null && Math.abs(tf - ff) < 1e-9) return 'move';
        return 'other';
    }

    // Заголовок и причина группы (бриф): «Посещать раз в 2 недели — 60 магазинов» · «заказывают реже…»
    function groupText(key, list) {
        if (key === 'transfer') return { title: 'Передать другому менеджеру', chip: 'Передать',
            reason: 'К этим магазинам ближе ездит другой менеджер или у него в этот день не хватает заказов. '
                + 'Вместе с магазином уходят его выручка и долг. Решение — по каждому магазину отдельно.' };
        if (key === 'incoming') return { title: 'Получает от других менеджеров', chip: 'Получает',
            reason: 'Эти магазины программа предлагает передать этому менеджеру. Решить можно здесь или у того, кто отдаёт.' };
        if (key === 'freq') {
            const half = list.every(c => Math.abs((num(c.to.freq) || 0) - 0.5) < 1e-9);
            return { title: half ? 'Посещать раз в 2 недели' : 'Посещать реже', chip: 'Реже',
                reason: 'Заказывают реже, чем их посещают, — визиты впустую.' };
        }
        if (key === 'move') return { title: 'Перенести на другой день', chip: 'Другой день',
            reason: 'Так день получается компактнее, а выручка по дням — ровнее.' };
        if (key === 'offday') {
            const days = uniqSorted(list.flatMap(c => offDaysOf(c.from.pattern)));
            if (days.length === 1 && WD_FROM[days[0]]) {
                const from = (days[0] === 2 || days[0] === 3 ? 'со ' : 'с ') + WD_FROM[days[0]];   // «со вторника», «с воскресенья»
                return { title: 'Перенести ' + from, chip: cap(from), reason: cap(WD_FULL[days[0]]) + ' — нерабочий день.' };
            }
            return { title: 'Перенести с нерабочих дней', chip: 'С выходных', reason: 'В эти дни менеджеры не работают.' };
        }
        if (key === 'winback') return { title: 'Постараться вернуть', chip: 'Вернуть',
            reason: 'Раньше покупали регулярно, сейчас перестали. Визит раз в 2 недели — чтобы попробовать вернуть.' };
        if (key === 'remove') return { title: 'Убрать из маршрута', chip: 'Убрать',
            reason: 'Не покупают больше ' + lostMonths() + ' или ни разу за год. Решение — по каждому магазину отдельно.' };
        return { title: 'Другие изменения', chip: 'Другое', reason: 'Меняются и дни, и число визитов.' };
    }

    // Почему программа это предлагает — одной-двумя фразами для «?» у магазина
    // Сколько уходит вместе с магазином: «выручка ≈ 120 тыс. драм в месяц, долг 45 тыс. драм»
    function moneyText(c) {
        const rev = num(c.revenue_month), debt = num(c.debt);
        const bits = [];
        if (rev !== null) bits.push('выручка ≈' + NB + moneyShort(rev) + NB + 'драм в месяц');
        if (debt !== null) bits.push(debt > 0 ? 'долг ' + moneyShort(debt) + NB + 'драм' : (debt < 0 ? 'переплата ' + moneyShort(-debt) + NB + 'драм' : 'долга нет'));
        return bits.join(', ');
    }

    function transferReason(c) {
        const to = agentName(c.to_agent), frm = agentName(c.from_agent);
        const why = {
            km: 'В этот район уже ездит ' + to + ' — так меньше км.',
            weak: 'У ' + to + ' в этот день не хватает заказов — с этим магазином день станет сильнее.',
            overload: 'У ' + frm + ' перегружен день — станет короче.',
            owner: 'Эту передачу вы уже приняли раньше.',
        }[c.reason_kind] || 'Отдельно эта передача почти ничего не меняет — выгода появляется вместе с другими изменениями.';
        const money = moneyText(c);
        return why + (money ? ' Вместе с магазином уходит ' + money + ' — планы продаж и кредитов обоих менеджеров нужно пересчитать.' : '');
    }

    function rowReason(c) {
        if (isTransfer(c)) return transferReason(c);
        const reason = String(c.reason || '');
        if (/шаблон принят владельцем/.test(reason)) return 'Эти дни вы уже приняли раньше — программа их сохранила.';
        if (/частота принята владельцем/.test(reason)) return 'Эту частоту визитов вы уже приняли раньше.';
        if (/удаление принято владельцем/.test(reason)) return 'Вы уже решили убрать этот магазин из маршрута.';
        const g = groupOf(c), silent = silenceText(c.customer_id);
        if (g === 'remove') return cap(silent || 'перестал покупать') + '.';
        if (g === 'winback') return cap(silent || 'перестал покупать') + '. Визит раз в 2 недели — чтобы попробовать вернуть.';
        const off = offDaysOf(c.from.pattern);
        const offText = off.length ? cap(off.map(d => WD_FULL[d]).join(' и ')) + (off.length > 1 ? ' — нерабочие дни.' : ' — нерабочий день.') : '';
        const ff = num(c.from.freq), tf = num(c.to.freq);
        if (ff !== null && tf !== null && tf < ff - 1e-9) {
            const rate = orderRateText(obj(custOf(c.customer_id)).lam_year);
            const season = /в сезон/.test(reason) ? ' В сезон заказывает чаще — это учтено.' : '';
            return (offText ? offText + ' ' : '') + (rate ? cap(rate) : 'Заказывает редко') + ', а посещают его ' + freqHuman(ff) + '.' + season;
        }
        return offText || 'Так день получается компактнее, а выручка по дням — ровнее.';
    }

    // Сколько км в неделю меняется у менеджера, если принять все его предложения (минус — меньше)
    const mgrKmDelta = (m) => {
        const b = m ? num(m.before.manager_km) : null, a = m ? num(m.after.manager_km) : null;
        return b !== null && a !== null ? a - b : null;
    };
    // «≈ 2 ч», «≈ 45 мин» — время округлено, чтобы не казалось точным
    function approxDur(mins) {
        const m = Math.abs(mins);
        if (m >= 60) return fmt(Math.round(m / 30) / 2, 1) + NB + 'ч';
        return Math.max(5, Math.round(m / 5) * 5) + NB + 'мин';
    }
    const visitsWord = (n) => (Number.isInteger(n) ? plural(n, 'визит', 'визита', 'визитов') : 'визита');

    // Эффект одного предложения, применённого к текущему плану, — коротко (в неделю). Перенос сам по себе
    // может добавить км: выгода — от того, как переставлены все магазины вместе, это и говорим
    // «у Армена −5,2 км, у Гора +1,1 км в неделю» — эффект передачи по обоим менеджерам
    function transferEffectText(c) {
        const side = (e, id) => {
            const km = num(obj(e).manager_km_week);
            return km !== null && round(km, 1) !== 0 ? 'у ' + agentName(id) + ' ' + (km < 0 ? MINUS : '+') + fmt(Math.abs(km), 1) + NB + 'км' : null;
        };
        const bits = [side(c.effect_from, c.from_agent), side(c.effect_to, c.to_agent)].filter(Boolean);
        const weak = num(c.effect.weak_days_week);
        if (weak !== null && Math.abs(weak) >= 0.1) bits.push('слабых дней ' + (weak < 0 ? MINUS : '+') + fmt(Math.abs(weak), 1));
        return bits.length ? 'Отдельно эта передача: ' + bits.join(', ') + ' в неделю.' : 'Отдельно эта передача почти не меняет км.';
    }

    function effectText(c, m) {
        if (isTransfer(c)) return transferEffectText(c);
        const e = c.effect;
        const km = num(e.manager_km_week), mins = num(e.minutes_week), weak = num(e.weak_days_week);
        const bits = [];
        if (km !== null && round(km, 1) !== 0) bits.push((km < 0 ? MINUS : '+') + fmt(Math.abs(km), 1) + NB + 'км');
        if (mins !== null && Math.round(mins) !== 0) bits.push((mins < 0 ? MINUS : '+') + durText(mins));
        if (weak !== null && Math.abs(weak) >= 0.1) bits.push('слабых дней ' + (weak < 0 ? MINUS : '+') + fmt(Math.abs(weak), 1));
        let text = bits.length ? 'Отдельно это изменение: ' + bits.join(', ') + ' в неделю.' : 'Отдельно это изменение почти не меняет км и время.';
        const total = mgrKmDelta(m);
        if (km !== null && round(km, 1) > 0 && total !== null && total < -1) {
            text += ' Вместе с остальными изменениями у менеджера выходит ' + MINUS + fmt(-total) + NB + 'км в неделю.';
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
                text: (incoming ? 'придёт ' : 'уйдёт ') + fmt(n) + ' ' + plural(n, 'магазин', 'магазина', 'магазинов')
                    + ' — ≈' + NB + moneyShort(rev) + NB + 'драм выручки в месяц',
                tip: 'Выручка — в среднем за месяц по заказам магазина за последние 12 месяцев. Долг этих магазинов на сегодня — '
                    + moneyShort(debt) + NB + 'драм (как на странице клиентов). Километры по группе не складываем: '
                    + 'выгода от передачи зависит от того, как переставлены все магазины вместе.' };
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
                text: 'на ' + fmt(n, 1) + ' ' + visitsWord(n) + ' в неделю ' + (dv < 0 ? 'меньше' : 'больше')
                    + (dv < 0 && mins <= -20 ? ' — это ≈' + NB + approxDur(mins) + ' работы' : ''),
                tip: 'Визиты посчитаны точно. Время — примерно: сумма по каждому магазину, если принимать их по одному.' };
        }
        const km = mgrKmDelta(m);
        return { tone: 0, text: 'визитов столько же — меняются только дни',
            tip: 'Сколько километров это сэкономит, зависит от того, как переставлены все магазины вместе.'
                + (km !== null && km < -1 ? ' Если принять все предложения этого менеджера, он будет проезжать на ' + fmt(-km) + NB + 'км в неделю меньше.' : '') };
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
        { key: 'revenue_week_low', mkey: 'revenue_low', word: 'зимой' },
        { key: 'revenue_week_peak', mkey: 'revenue_peak', word: 'летом' },
        { key: 'revenue_week_year', mkey: 'revenue_year', word: 'в среднем за год' },
    ];
    const truckTipText = () => 'Машина закреплена за водителем, а не за менеджером: заказы всех менеджеров за день развозятся по машинам '
        + 'и рейсам вместе — с учётом тоннажа и рабочего дня машины. Поэтому дни визитов разных менеджеров связаны: если заказы из одного района '
        + 'приходятся на один день доставки, машина едет туда один раз. Литры — км рейса × расход машины, которая его везёт; в среднем за год. '
        + 'Рейсы, загрузка и км парка — в «Все цифры».';
    const kmTipText = () => 'Сколько километров менеджеры проезжают за неделю: из дома к магазинам дня в самом коротком порядке и обратно домой. '
        + (state.roads ? 'Км — по дорогам на карте.' : 'Км — по прямой с поправкой на извилистость дорог.')
        + ' Литры и драмы — по расходу машин менеджеров и ценам топлива из настроек.';
    function weakTipText() {
        const months = monthsText(state.season && state.season.low_months);
        const days = num(state.result.after.days_total) ?? num(state.result.before.days_total);
        return 'Считаем по заказам зимы' + (months ? ' (' + months + ')' : '') + ' — это самое слабое время года. День слабый, '
            + 'если он скорее всего не наберёт ' + minText() + ' (шанс меньше 50%).'
            + (days !== null ? ' Всего рабочих дней у менеджеров — ' + fmt(days, 1) + ' в неделю.' : '');
    }

    function renderMain() {
        const r = state.result, list = $('roMainList'), note = $('roMainNote');
        list.textContent = '';
        note.textContent = '';
        note.hidden = true;
        const items = [];
        const n = allChanges().length;
        $('roMainLead').textContent = n ? 'Если принять все предложения:' : 'Текущий план уже близок к лучшему.';

        // 0. Дизель грузовиков — первой строкой (парк: заказы всех менеджеров развозят машины вместе)
        const tlb = num(r.before.truck_liters_week), tla = num(r.after.truck_liters_week);
        const link = (hash, text) => h('a', { href: '/routes/settings#' + hash, text });
        if (tlb !== null && tla !== null) {
            const dl = tlb - tla, ab = num(r.before.truck_amd_week), aa = num(r.after.truck_amd_week);
            const money = ab !== null && aa !== null ? ' (≈' + NB + moneyRound(ab) + ' → ' + moneyRound(aa) + NB + 'драм)' : '';
            items.push({ tone: Math.abs(dl) < 1 ? 0 : (dl > 0 ? 1 : -1), tip: truckTipText(), tipLabel: 'дизель грузовиков',
                nodes: Math.abs(dl) < 1
                    ? ['дизель грузовиков почти не изменится — ', mark(fmt(tla) + NB + 'л в неделю', 0), money]
                    : ['дизель грузовиков: ', mark(fmt(tlb) + ' → ' + fmt(tla) + NB + 'л в неделю', dl > 0 ? 1 : -1),
                        money, ' — на ' + fmt(Math.abs(dl)) + NB + 'л ' + (dl > 0 ? 'меньше' : 'больше')] });
        } else {
            note.append(icon('fa-truck'), h('span', {}, depotOf()
                ? ['Укажите тоннаж и расход машин ', link('trucks', 'в настройках'), ' — тогда программа будет экономить дизель грузовиков.']
                : ['Укажите склад и машины ', link('depot', 'в настройках'), ' — тогда программа будет экономить дизель грузовиков.']));
            note.hidden = false;
        }

        // 1. Километры менеджеров: литры и драмы, если посчитаны
        const kb = total('before', 'manager_km_week', 'manager_km'), ka = total('after', 'manager_km_week', 'manager_km');
        if (kb !== null && ka !== null) {
            const d = kb - ka;
            if (Math.abs(d) < 1) {
                items.push({ tone: 0, nodes: ['пробег менеджеров почти не изменится — ', mark(fmt(ka) + NB + 'км в неделю', 0)], tip: kmTipText(), tipLabel: 'километры' });
            } else {
                const extra = [];
                const lb = num(r.before.manager_liters_week), la = num(r.after.manager_liters_week);
                if (lb !== null && la !== null && Math.abs(lb - la) >= 1) extra.push('≈' + NB + fmt(Math.abs(lb - la)) + NB + 'л топлива');
                const ab = num(r.before.manager_amd_week), aa = num(r.after.manager_amd_week);
                if (ab !== null && aa !== null && Math.abs(ab - aa) >= 100) extra.push('≈' + NB + moneyRound(Math.abs(ab - aa)) + NB + 'драм');
                items.push({ tone: d > 0 ? 1 : -1, tip: kmTipText(), tipLabel: 'километры',
                    nodes: ['менеджеры будут проезжать ', mark('на ' + fmt(Math.abs(d)) + NB + 'км в неделю ' + (d > 0 ? 'меньше' : 'больше'), d > 0 ? 1 : -1),
                        extra.length ? ' (' + extra.join(', ') + ')' : ''] });
            }
        }

        // 2. Слабые дни зимой
        const wb = total('before', 'days_below_min', 'days_below_min'), wa = total('after', 'days_below_min', 'days_below_min');
        if (wb !== null && wa !== null) {
            const phrase = 'дней, когда выручка меньше ' + minText() + ', зимой ';
            const rb = round(wb, 1), ra = round(wa, 1);
            if (ra < rb) items.push({ tone: 1, nodes: [phrase + 'станет ', mark(fmt(wa, 1) + ' вместо ' + fmt(wb, 1), 1)], tip: weakTipText(), tipLabel: 'слабые дни' });
            else if (ra > rb) items.push({ tone: -1, nodes: [phrase + 'станет больше: ', mark(fmt(wa, 1) + ' вместо ' + fmt(wb, 1), -1)], tip: weakTipText(), tipLabel: 'слабые дни' });
            else if (!ra) items.push({ tone: 1, nodes: ['дней, когда выручка меньше ' + minText() + ', зимой ', mark('нет и не будет', 1)], tip: weakTipText(), tipLabel: 'слабые дни' });
            else items.push({ tone: 0, nodes: [phrase + 'останется столько же — ', mark(fmt(wa, 1), 0)], tip: weakTipText(), tipLabel: 'слабые дни' });
        }

        // 3. Визиты и длина рабочего дня
        const vb = total('before', 'visits_week', 'visits'), va = total('after', 'visits_week', 'visits');
        const pb = total('before', 'avg_plan_hours', 'avg_plan_hours', true), pa = total('after', 'avg_plan_hours', 'avg_plan_hours', true);
        if (vb !== null && va !== null && vb > 0) {
            const dv = vb - va, p = Math.round(Math.abs(dv) / vb * 100);
            const shift = pb !== null && pa !== null ? dayShift((pb - pa) * 60) : null;
            const nodes = [];
            if (Math.abs(dv) < 0.5) nodes.push('визитов будет столько же — ', mark(fmt(va) + ' в неделю', 0));
            else nodes.push('визитов станет ', mark('на ' + (p ? p + '%' : fmt(Math.abs(dv), 1)) + ' ' + (dv > 0 ? 'меньше' : 'больше'), dv > 0 ? 1 : -1),
                ' — ' + fmt(va, 1) + ' вместо ' + fmt(vb, 1) + ' в неделю');
            if (shift) nodes.push(', а рабочий день — ' + shift);
            items.push({ tone: dv > 0.4 ? 1 : (dv < -0.4 ? -1 : 0), nodes, tipLabel: 'визиты',
                tip: 'Визит — один заход менеджера в магазин. Рабочий день — от выезда из дома до возвращения, в среднем'
                    + (pb !== null && pa !== null ? ': сейчас ' + fmt(pb, 1) + NB + 'ч, станет ' + fmt(pa, 1) + NB + 'ч.' : '.') });
        }

        // 4. Выручка — не меняется или меняется
        const rows = SEASONS.map(s => ({ s, b: total('before', s.key, s.mkey), a: total('after', s.key, s.mkey) }))
            .filter(x => x.b !== null && x.a !== null);
        if (rows.length) {
            const lost = rows.filter(x => x.a < x.b - 1);   // 1 драм — округление
            const now = rows.map(x => x.s.word + ' ≈' + NB + moneyShort(x.b)).join(', ');
            const tipText = 'Программа снижает частоту только там, где магазин заказывает реже, чем его посещают, поэтому ожидаемая выручка не падает. '
                + 'Ожидаемая выручка в неделю сейчас: ' + now + ' драм. У тех, кто перестал покупать, её уже нет — их визиты ничего не приносят.';
            if (!lost.length) items.push({ tone: 1, nodes: [mark('выручка не уменьшится', 1), ' — ни зимой, ни летом'], tip: tipText, tipLabel: 'выручка' });
            else items.push({ tone: -1, tip: tipText, tipLabel: 'выручка',
                nodes: ['ожидаемая выручка уменьшится: ', mark(lost.map(x => x.s.word + ' на ' + fmt(Math.abs((x.a - x.b) / x.b * 100), 1) + '%').join(', '), -1)] });
        }

        // 5. Передачи между менеджерами — одной строкой (только в расчёте с передачами)
        if (transferMode()) {
            const t = obj(r.transfers), nt = num(t.count) || 0;
            const tipText = 'Магазин предлагается отдать другому менеджеру, если тот и так ездит рядом или у него в этот день не хватает заказов. '
                + 'Передача должна экономить заметно — больше ' + fmt(num(t.penalty_week) ?? 2000) + NB + 'драм в неделю, иначе отношения с магазином рвать незачем. '
                + 'Выручка компании от передач не меняется, но меняются планы продаж и кредитов менеджеров: сколько каждый отдаёт и получает — в карточках менеджеров.';
            if (nt) {
                items.push({ tone: 1, tip: tipText, tipLabel: 'передачи',
                    nodes: [mark('передать ' + fmt(nt) + ' ' + plural(nt, 'магазин', 'магазина', 'магазинов'), 1),
                        ' другим менеджерам (≈' + NB + moneyShort(t.revenue_month) + NB + 'драм выручки в месяц) — это уже учтено в цифрах выше'] });
            } else {
                items.push({ tone: 0, tip: tipText, tipLabel: 'передачи',
                    nodes: ['передавать магазины между менеджерами ', mark('невыгодно', 0), ' — ни одна передача не экономит заметно'] });
            }
        }

        items.forEach(it => list.append(h('li', { class: it.tone > 0 ? 'is-good' : (it.tone < 0 ? 'is-bad' : 'is-same') },
            h('span', { class: 'ico', 'aria-hidden': 'true' }, icon(it.tone > 0 ? 'fa-check' : (it.tone < 0 ? 'fa-arrow-up' : 'fa-equals'))),
            h('span', { class: 'txt' }, it.nodes, it.tip ? [' ', tip(it.tip, it.tipLabel)] : null))));
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
                h('span', { class: 'rt-sr-only', text: 'сейчас ' }), h('span', { class: 'was', text: fmt(o.was, o.d) }),
                h('span', { class: 'arr', 'aria-hidden': 'true', text: '→' }),
                h('span', { class: 'rt-sr-only', text: ', если принять — ' }), h('span', { class: 'now', text: fmt(o.now, o.d) })),
            h('div', { class: 'rt-kpi-delta' }, both ? (t ? signed(diff, o.d) + unit + pctText : 'без изменений') : 'не посчитано'));
    }

    function renderKpis() {
        const box = $('roKpis');
        box.textContent = '';
        // слово к разнице: «−412 км», «−14 дней», «−465 визитов»; дробное — «3,5 дня»
        const word = (one, few, many) => (v) => (Number.isInteger(v) ? plural(v, one, few, many) : few);
        box.append(
            kpiNode({ label: 'Км в неделю', was: total('before', 'manager_km_week', 'manager_km'), now: total('after', 'manager_km_week', 'manager_km'),
                d: 0, lower: true, unit: () => 'км', tip: kmTipText() }),
            kpiNode({ label: 'Слабых дней зимой', was: total('before', 'days_below_min', 'days_below_min'), now: total('after', 'days_below_min', 'days_below_min'),
                d: 1, lower: true, unit: word('день', 'дня', 'дней'), tip: 'Слабый день — день, когда выручка меньше ' + minText() + '. ' + weakTipText() }),
            kpiNode({ label: 'Визитов в неделю', was: total('before', 'visits_week', 'visits'), now: total('after', 'visits_week', 'visits'),
                d: 1, lower: true, unit: word('визит', 'визита', 'визитов'), tip: 'Сколько раз за неделю менеджеры заходят в магазины — по всем менеджерам расчёта. '
                    + 'Визит «раз в 2 недели» считается как половина визита в неделю.' }));
    }

    // ---------- «Все цифры» ----------
    function renderAllTable() {
        const r = state.result, tb = $('roAllTable').tBodies[0];
        tb.textContent = '';
        const days = num(r.after.days_total) ?? num(r.before.days_total);
        const rows = [
            { label: 'Визитов в неделю', b: total('before', 'visits_week', 'visits'), a: total('after', 'visits_week', 'visits'), d: 1, lower: true },
            { label: 'Дней с выручкой меньше ' + minText() + ' зимой' + (days !== null ? ' (из ' + fmt(days, 1) + ')' : ''),
                b: total('before', 'days_below_min', 'days_below_min'), a: total('after', 'days_below_min', 'days_below_min'), d: 1, lower: true },
            { label: 'Км менеджеров в неделю', b: total('before', 'manager_km_week', 'manager_km'), a: total('after', 'manager_km_week', 'manager_km'), d: 0, lower: true },
            { label: 'Топливо менеджеров, литров в неделю', b: num(r.before.manager_liters_week), a: num(r.after.manager_liters_week), d: 0, lower: true },
            { label: 'Топливо менеджеров, драм в неделю', b: num(r.before.manager_amd_week), a: num(r.after.manager_amd_week), d: 0, lower: true,
                none: 'не посчитано — нет цен топлива' },
            { label: 'Дизель грузовиков, литров в неделю', b: num(r.before.truck_liters_week), a: num(r.after.truck_liters_week),
                d: 0, lower: true, none: 'не посчитан — укажите тоннаж и расход машин' },
            { label: 'Дизель грузовиков, драм в неделю', b: num(r.before.truck_amd_week), a: num(r.after.truck_amd_week), d: 0, lower: true,
                none: 'не посчитано — нет машин или цены дизеля' },
            { label: 'Км грузовиков в неделю', b: num(r.before.truck_km_week), a: num(r.after.truck_km_week), d: 0, lower: true,
                none: 'не посчитано — нет склада или машин' },
            { label: 'Рейсов грузовиков в неделю', b: num(r.before.trips_week), a: num(r.after.trips_week), d: 1, lower: true, none: 'не посчитано' },
            { label: 'Рейсов в день доставки', b: num(r.before.trips_per_day), a: num(r.after.trips_per_day), d: 1, lower: true, none: 'не посчитано' },
            { label: 'Загрузка машины в рейсе, %', b: num(r.before.avg_load_pct), a: num(r.after.avg_load_pct), d: 0, lower: false, none: 'не посчитано' },
            { label: 'Загрузка в пик (летом), %', b: num(r.before.avg_load_pct_peak), a: num(r.after.avg_load_pct_peak), d: 0, lower: null, none: 'не посчитано' },
            { label: 'Машино-часов в неделю', b: num(r.before.truck_hours_week), a: num(r.after.truck_hours_week), d: 0, lower: true, none: 'не посчитано' },
            { label: 'Дней доставки, когда в пик машины не успевают', b: num(r.before.truck_days_short_week), a: num(r.after.truck_days_short_week),
                d: 1, lower: true, none: 'не посчитано' },
            { label: 'Время у магазинов, часов в день', b: total('before', 'avg_plan_work_hours', 'avg_work_hours', true),
                a: total('after', 'avg_plan_work_hours', 'avg_work_hours', true), d: 1, lower: null },
            { label: 'Рабочий день с дорогой из дома, часов', b: total('before', 'avg_plan_hours', 'avg_plan_hours', true),
                a: total('after', 'avg_plan_hours', 'avg_plan_hours', true), d: 1, lower: true },
        ].concat(SEASONS.map(s => ({ label: 'Ожидаемая выручка в неделю ' + s.word + ', драм', b: total('before', s.key, s.mkey),
            a: total('after', s.key, s.mkey), money: true, lower: false })));
        rows.forEach(x => {
            const both = x.b !== null && x.a !== null;
            const f = (v) => (x.money ? moneyShort(v) : fmt(v, x.d));
            let diff = '—', cls = '';
            if (both) {
                const dd = x.a - x.b, rr = x.money ? Math.round(dd / 1000) : round(dd, x.d);
                if (!rr) diff = 'без изменений';
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
                    : h('td', { class: 'muted', colspan: '3', text: x.none || 'не посчитано' })));
        });
        // Сколько предложений в каких группах и откуда данные
        const cnt = {};
        allChanges().forEach(c => { const g = groupOf(c); cnt[g] = (cnt[g] || 0) + 1; });
        const words = { transfer: 'передать другому менеджеру', freq: 'посещать реже', move: 'на другой день', offday: 'с нерабочего дня',
            winback: 'вернуть', remove: 'убрать', other: 'другое' };
        const foot = $('roAllFoot');
        foot.textContent = '';
        const n = allChanges().length;
        const snap = parseTime(r.snapshot_as_of);
        foot.append((n ? 'Предложений: ' + fmt(n) + ' — ' + GROUP_ORDER.filter(g => cnt[g]).map(g => words[g] + ' ' + fmt(cnt[g])).join(', ') + '. '
            : 'Предложений нет. ') + (snap ? 'Данные ERP на ' + dayTime(snap) + '.' : ''));
        if (r.fuel_price_source === 'fallback') {
            foot.append(' Цены топлива в настройках не указаны — при выборе дней программа брала условную цену '
                + fmt(r.fuel_price_used) + NB + 'драм за литр. ', h('a', { href: '/routes/settings#fuel', text: 'Указать цены' }), '.');
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
        if (k === 0) return 'Зимой заказов не хватает ни на один день по ' + minText() + ' — нужны новые магазины.';
        return 'Зимой заказов хватает на ' + k + ' ' + plural(k, 'день', 'дня', 'дней') + ' из ' + wd + ' — чтобы каждый день приносил '
            + minText() + ', нужны новые магазины.';
    }

    // Баланс передач менеджера (ответ владельца №17): «отдаёт 3 магазина (≈ 450 тыс. драм в месяц) · получает 1 (…)»
    function balanceNode(m) {
        if (!transferMode() || !m.balance) return null;
        const g = m.balance.given, r = m.balance.received;
        const ng = num(g.stores) || 0, nr = num(r.stores) || 0;
        if (!ng && !nr) return h('p', { class: 'ro-card-bal is-none', text: 'Магазины не передаёт и не получает' });
        const part = (word, n, x) => word + ' ' + fmt(n) + ' ' + plural(n, 'магазин', 'магазина', 'магазинов')
            + ' (≈' + NB + moneyShort(x.revenue_month) + NB + 'драм в месяц)';
        const text = [ng ? part('отдаёт', ng, g) : null, nr ? part('получает', nr, r) : null].filter(Boolean).join(' · ');
        const debt = (x) => moneyShort(num(x.debt) || 0) + NB + 'драм';
        return h('p', { class: 'ro-card-bal' }, icon('fa-people-arrows'), h('span', { text: cap(text) }),
            tip('Если принять все передачи этого менеджера. Выручка — в среднем за месяц по заказам магазинов за 12 месяцев. Долг магазинов: '
                + (ng ? 'уходит ' + debt(g) : '') + (ng && nr ? ', ' : '') + (nr ? 'приходит ' + debt(r) : '')
                + '. По этим цифрам пересчитайте планы продаж и кредитов.', 'баланс передач'));
    }

    function cardNode(m) {
        const n = m.changes.length, open = state.panelAgent === String(m.agent_id);
        const sum = [];
        const kb = num(m.before.manager_km), ka = num(m.after.manager_km);
        if (kb !== null && ka !== null) {
            const d = ka - kb;
            sum.push(Math.abs(d) < 1 ? h('span', { text: 'км почти без изменений' })
                : h('span', { class: d < 0 ? 'is-good' : 'is-bad', text: (d < 0 ? MINUS : '+') + fmt(Math.abs(d)) + NB + 'км в неделю' }));
        }
        const wb = num(m.before.days_below_min), wa = num(m.after.days_below_min);
        if (wb !== null && wa !== null) {
            sum.push(!wb && !wa ? h('span', { text: 'слабых дней нет' })
                : h('span', { class: wa < wb ? 'is-good' : (wa > wb ? 'is-bad' : ''), text: 'слабых дней ' + fmt(wb, 1) + ' → ' + fmt(wa, 1) }));
        }
        sum.push(h('span', { text: n ? fmt(n) + ' ' + plural(n, 'предложение', 'предложения', 'предложений') : 'изменений нет' }));
        const st = mgrProgress(m), decided = st.accepted + st.rejected + st.mixed;
        const warn = feasText(m);
        const bal = balanceNode(m);
        const btn = (n || m.hints.length || incomingOf(m).length) ? h('button', { type: 'button', class: 'rt-btn ' + (open ? 'rt-btn-primary' : 'rt-btn-ghost') + ' ro-card-btn',
            'aria-expanded': String(open), 'aria-controls': 'roPanel', dataset: { open: String(m.agent_id) },
            'aria-label': (open ? 'Свернуть предложения: ' : 'Посмотреть предложения: ') + mgrName(m) },
            h('span', { text: open ? 'Свернуть предложения' : (n || incomingOf(m).length ? 'Посмотреть предложения' : 'Посмотреть подсказки') }),
            icon(open ? 'fa-chevron-up' : 'fa-arrow-down')) : null;
        return h('li', { class: 'ro-card' + (open ? ' is-open' : ''), dataset: { agent: String(m.agent_id) } },
            h('div', { class: 'ro-card-head' },
                h('span', { class: 'rt-dot', style: 'background:' + m._color, 'aria-hidden': 'true' }),
                h('span', { class: 'ro-card-name', text: mgrName(m) }),
                m.code ? h('span', { class: 'ro-card-code', text: String(m.code) }) : null),
            h('p', { class: 'ro-card-sum' }, sum.map((x, i) => (i ? [h('span', { class: 'sep', 'aria-hidden': 'true', text: ' · ' }), x] : x))),
            bal,
            warn ? h('p', { class: 'ro-card-warn' }, icon('fa-triangle-exclamation'), h('span', { text: warn })) : null,
            m.time_capped === true ? h('p', { class: 'ro-card-note', text: 'Расчёт остановлен по времени — повторный может немного отличаться.' }) : null,
            n ? h('div', { class: 'ro-card-prog' },
                h('span', { class: 'bar', 'aria-hidden': 'true' },
                    h('span', { class: 'ok', style: 'width:' + ((st.accepted + st.mixed) / n * 100).toFixed(1) + '%' }),
                    h('span', { class: 'off', style: 'width:' + (st.rejected / n * 100).toFixed(1) + '%' })),
                h('span', { class: 'txt', text: decided ? 'решено ' + fmt(decided) + ' из ' + fmt(n) : 'решений пока нет' })) : null,
            btn);
    }

    function renderCards() {
        const ul = $('roCards');
        ul.textContent = '';
        if (!state.result.managers.length) {
            ul.append(h('li', { class: 'rt-placeholder', text: 'В расчёте нет менеджеров.' }));
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
        announce('Открыты предложения: ' + mgrName(m));
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
                h('span', { class: 'pre', text: 'Предложения — ' }), h('span', { class: 'n', text: mgrName(m) }),
                m.code ? h('span', { class: 'c', text: String(m.code) }) : null),
            h('div', { class: 'ro-panel-acts' },
                h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { pact: 'map' }, 'aria-label': 'Показать на карте: ' + mgrName(m) },
                    icon('fa-map-location-dot'), 'На карте'),
                h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { pact: 'close' }, 'aria-label': 'Свернуть предложения: ' + mgrName(m) },
                    icon('fa-xmark'), 'Свернуть'))));
        state.panelStats = h('p', { class: 'ro-panel-stats' });
        box.append(state.panelStats);

        if (!total && !m.hints.length) {
            box.append(h('p', { class: 'rt-placeholder', text: 'Программа не предлагает ничего менять у этого менеджера.' }));
            renderPanelStats();
            return;
        }

        // Переключатели групп и поиск
        const chips = h('div', { class: 'rt-chips ro-gchips', role: 'group', 'aria-label': 'Какие предложения показать' },
            h('button', { type: 'button', class: 'rt-chip', dataset: { pgroup: 'all' } }, 'Все', h('span', { class: 'cnt', text: fmt(total) })),
            keys.map(k => h('button', { type: 'button', class: 'rt-chip g-' + k, dataset: { pgroup: k } },
                groupText(k, by.get(k)).chip, h('span', { class: 'cnt', text: fmt(by.get(k).length) }))),
            m.hints.length ? h('button', { type: 'button', class: 'rt-chip g-hints', dataset: { pgroup: 'hints' } }, 'Подсказки',
                h('span', { class: 'cnt', text: fmt(m.hints.length) })) : null);
        const search = h('label', { class: 'ro-search', for: 'roPanelSearch' },
            icon('fa-magnifying-glass'), h('span', { class: 'rt-sr-only', text: 'Найти магазин по названию или коду' }),
            h('input', { type: 'search', id: 'roPanelSearch', class: 'rt-input', placeholder: 'Найти магазин…', autocomplete: 'off', spellcheck: 'false' }));
        box.append(h('div', { class: 'ro-panel-tools' }, chips, search));
        search.querySelector('input').value = state.pf.raw || '';   // панель перерисовали — поиск остаётся

        const wrap = h('div', { class: 'ro-grps' });
        keys.forEach(k => wrap.append(groupNode(m, k, by.get(k))));
        if (m.hints.length) wrap.append(hintsNode(m));
        state.noMatch = h('p', { class: 'rt-placeholder', hidden: true, text: 'Ничего не нашлось — проверьте название или код магазина.' });
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
                        'Если принять всю группу: ', h('b', { text: eff.text }), ' ', tip(eff.tip, 'эффект группы'))),
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
        g.listEl = h('ul', { class: 'ro-list', id: id + '_l', 'aria-label': 'Подсказки', hidden: true });
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
                    h('h4', { class: 'ro-grp-title', id: id + '_t' }, 'Подсказки', ' — ', g.count),
                    h('p', { class: 'ro-grp-reason', text: 'Эти магазины заказывают чаще, чем их посещают. Программа это не меняет — решите сами.' })),
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
                h('span', { class: 'lbl', text: 'было: ' }), h('span', { class: 'from', text: (transfer ? 'у ' + agentName(c.from_agent) + ' — ' : '') + patternHuman(c.from.pattern) }),
                h('span', { class: 'arr', 'aria-hidden': 'true', text: ' → ' }),
                h('span', { class: 'lbl', text: 'станет: ' }),
                h('span', { class: 'to', text: isRemove(c) ? 'не посещать' : (transfer ? 'у ' + agentName(c.to_agent) + ' — ' : '') + patternHuman(c.to.pattern) }),
                ' ', tip(details, 'почему — ' + custName(id))),
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
            'aria-disabled': c._saving ? 'true' : null, 'aria-label': (label || text) + ': ' + name }, ico ? icon(ico) : null, text);
        const S = st === 'none' ? null : ((remove ? REMOVE_ROW_STATUS[st] : (transfer ? TRANSFER_ROW_STATUS[st] : ROW_STATUS[st])) || ROW_STATUS.mixed);
        if (c._saving) r.side.append(h('span', { class: 'ro-pill' }, spin(), 'Сохраняю…'));
        else if (S) r.side.append(h('span', { class: 'ro-pill ' + S.cls }, icon(S.icon), S.text));
        const acts = h('div', { class: 'ro-item-acts' });
        if (remove) {
            if (st === 'none') acts.append(btn('ro-btn-remove', 'fa-user-minus', 'Убрать…', 'accept', 'Убрать из маршрута'),
                btn('rt-btn-ghost', null, 'Оставить', 'reject', 'Оставить в маршруте'));
            else acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Отменить', 'reset', 'Отменить решение'));
        } else if (transfer) {
            if (st === 'none') acts.append(btn('ro-btn-ok', 'fa-people-arrows', 'Передать…', 'accept', 'Передать менеджеру ' + agentName(c.to_agent)),
                btn('rt-btn-ghost', null, 'Оставить', 'reject', 'Оставить у ' + agentName(c.from_agent)));
            else acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Отменить', 'reset', 'Отменить решение'));
            acts.append(btn('rt-btn-ghost', 'fa-map-location-dot', 'На карте', 'map', 'Показать передачу на карте'));
        } else {
            if (st === 'none' || st === 'mixed') acts.append(btn('ro-btn-ok', 'fa-check', 'Принять', 'accept'));
            if (st === 'none') acts.append(btn('rt-btn-ghost', null, 'Оставить как есть', 'reject'));
            if (st !== 'none') acts.append(btn('rt-btn-ghost', 'fa-rotate-left', 'Отменить', 'reset', 'Отменить решение'));
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
        g.count.textContent = (state.pf.q ? 'найдено ' : '') + fmt(n) + ' ' + plural(n, 'магазин', 'магазина', 'магазинов');
        g.toggle.querySelector('span').textContent = g.open ? 'Скрыть магазины' : 'Показать магазины';
        if (g.key === 'hints') { g.state.textContent = 'к сведению'; return; }
        let acc = 0, rej = 0, pending = 0;
        g.list.forEach(c => {
            const st = statusOf(c);
            if (st === 'accepted' || st === 'mixed') acc++;
            else if (st === 'rejected') rej++;
            if (bulkable(c)) pending++;
        });
        g.state.textContent = '';
        if (acc + rej === total) g.state.append(icon('fa-circle-check'), ' решено по всем');
        else g.state.append((acc ? 'принято ' + fmt(acc) : 'принято 0') + ' из ' + fmt(total) + (rej ? ' · оставлено ' + fmt(rej) : ''));
        g.state.classList.toggle('is-done', acc + rej === total);
        if (g.accept) {
            const word = state.pf.q ? 'Принять найденные' : 'Принять все в группе';
            g.accept.querySelector('span').textContent = g.busy ? 'Сохраняю…' : word + ' (' + fmt(pending) + ')';
            g.accept.setAttribute('aria-disabled', g.busy || !pending ? 'true' : 'false');
            g.accept.hidden = !pending && !g.busy;
            g.accept.setAttribute('aria-label', word + ': ' + groupText(g.key, g.list).title + ', без решения — ' + pending);
        }
    }

    function renderPanelStats() {
        const m = panelMgr(), el = state.panelStats;
        if (!m || !el) return;
        const n = m.changes.length, st = mgrProgress(m), inc = incomingOf(m).length;
        el.textContent = '';
        const incText = inc ? 'получает от других менеджеров ' + fmt(inc) + ' ' + plural(inc, 'магазин', 'магазина', 'магазинов') : '';
        if (!n) { el.append('Своих предложений нет', inc ? ' — ' + incText + '.' : (m.hints.length ? ' — только подсказки.' : '.')); return; }
        el.append(h('b', { text: fmt(n) }), ' ' + plural(n, 'предложение', 'предложения', 'предложений'));
        if (st.accepted + st.mixed) el.append(' · принято ', h('b', { text: fmt(st.accepted + st.mixed) }));
        if (st.rejected) el.append(' · оставлено как есть ', h('b', { text: fmt(st.rejected) }));
        if (inc) el.append(' · ' + incText);
        el.append('. Примите группу целиком или откройте магазины и решите по каждому.');
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
                const ok = window.confirm('Передать «' + custName(r.c.customer_id) + '» от ' + agentName(r.c.from_agent) + ' к '
                    + agentName(r.c.to_agent) + '?\n\n'
                    + (money ? 'Вместе с магазином уходит ' + money + ' — пересчитайте планы продаж и кредитов обоих менеджеров. ' : '')
                    + 'В ERP ничего не изменится, пока вы сами не внесёте план. Решение можно отменить.');
                if (!ok) return;
            }
            if (b.dataset.act === 'accept' && isRemove(r.c)) {
                const ok = window.confirm('Убрать «' + custName(r.c.customer_id) + '» из маршрута?\n\n'
                    + 'Менеджер перестанет посещать этот магазин. В ERP ничего не изменится, пока вы сами не внесёте план. '
                    + 'Решение можно отменить.');
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
        if (!list.length) { announce('В этой группе нет предложений без решения'); return; }
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
    const whyText = (err) => (err.network ? 'нет связи с сервером' : (err.status === 401 ? 'нужно войти заново'
        : String(err.message || 'ошибка сервера').replace(/[.!]\s*$/, '')));

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
                c._error = own ? 'Не сохранилось: ' + own.replace(/[.!]\s*$/, '') + '.'
                    : (rowErr.size ? 'Не сохранилось вместе с другими — в списке есть ошибка. Нажмите ещё раз.'
                        : 'Не сохранилось: ' + whyText(err) + '. Нажмите ещё раз.');
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
        const done = (one ? { accept: 'Будет убран из маршрута', reject: 'Остаётся в маршруте', reset: 'Решение отменено' }
            : (moved ? { accept: 'Будет передан', reject: 'Остаётся у своего менеджера', reset: 'Решение отменено' }
                : { accept: 'Принято', reject: 'Оставлено как есть', reset: 'Решение отменено' }))[action];
        const who = todo.length === 1 ? ': ' + custName(todo[0].customer_id) : ' ' + fmt(todo.length);
        announce(err ? 'Не сохранилось' + who + '. ' + sentence(cap(whyText(err))) : done + who);
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
            el.append(icon('fa-circle-check'), h('span', {}, 'Вы приняли ', h('b', { text: fmt(acc) + ' ' + plural(acc, 'изменение', 'изменения', 'изменений') }),
                ' у ' + mgrs.size + ' ' + plural(mgrs.size, 'менеджера', 'менеджеров', 'менеджеров') + '.'));
            el.classList.add('is-done');
        } else {
            el.append(h('span', { text: 'Вы пока ничего не приняли. Откройте менеджера в шаге 2 и нажмите «Принять» — принятое попадёт в план для ERP.' }));
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
            line(h('b', { text: fmt(stale) + ' ' + plural(stale, 'решение больше не действует', 'решения больше не действуют', 'решений больше не действуют') }),
                ': план этих магазинов в ERP изменился после вашего решения, поэтому ' + plural(stale, 'оно не входит', 'они не входят', 'они не входят')
                + ' ни в расчёт, ни в файл. ', h('button', { type: 'button', class: 'rt-linkbtn', dataset: { showdec: '1' }, text: 'Показать' }));
        }
        const mm = state.decisions ? dataMismatch(state.decisions.data_as_of) : null;
        if (mm) line('Данные ERP обновились после расчёта (расчёт — на ' + stamp(mm.calc) + ', сейчас — на ' + stamp(mm.now)
            + '). Нажмите «Пересчитать» в шаге 1, чтобы предложения совпали с текущим планом.');
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
            state.decError = e.network ? 'нет связи с сервером' : whyText(e);
        }
        renderDecisions();
        renderExport();
        renderStepWarn();
    }

    const decName = (x) => (x.customer_name ? String(x.customer_name) : 'Магазин ' + x.customer_id);
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
        errBox.textContent = state.decError ? 'Список решений не загрузился: ' + state.decError + '.' : '';
        if (!d) {
            counts.append(state.decError ? '—' : 'Загружаю решения…');
            $('roDecSum').textContent = '';
            reset.setAttribute('aria-disabled', 'true');
            return;
        }
        const s = d.summary, items = d.decisions;
        const acc = num(s.accepted) || 0, rej = num(s.rejected) || 0, stale = num(s.stale) || 0;
        $('roDecSum').textContent = items.length ? fmt(items.length) : 'пока нет';
        if (!items.length) {
            counts.append('Решений нет — примите или оставьте предложения в шаге 2.');
        } else {
            counts.append('принято ', h('b', { text: fmt(acc) }), ' · оставлено как есть ', h('b', { text: fmt(rej) }));
            if (stale) counts.append(' · ', h('b', { class: 'is-warn', text: fmt(stale) }), ' ' + plural(stale, 'устарело', 'устарели', 'устарели'));
        }
        reset.setAttribute('aria-disabled', !items.length || state.decBusy ? 'true' : 'false');
        list.hidden = !items.length;
        items.forEach((x, i) => list.append(decRow(x, i)));
    }

    // Решение словами: «дни: пн каждую неделю → чт раз в 2 недели», «как часто: раз в неделю → раз в 2 недели»
    function decChange(x) {
        if (x.kind === 'transfer') {
            const v = obj(x.value);
            return { label: 'передать', from: (x.agent_code ? 'у ' + x.agent_code + ' — ' : '') + (x.from ? patternHuman(x.from) : humanPlanText(x.from_text)),
                to: (v.agent_code ? 'у ' + v.agent_code + ' — ' : '') + (Array.isArray(v.pattern) ? patternHuman(v.pattern) : humanPlanText(x.to_text)) };
        }
        if (x.kind === 'remove') return { label: 'убрать', from: x.from ? patternHuman(x.from) : humanPlanText(x.from_text), to: 'не посещать' };
        if (x.kind === 'freq') return { label: 'как часто', from: x.from !== null && x.from !== undefined ? freqHuman(x.from) : humanPlanText(x.from_text),
            to: x.value !== null && x.value !== undefined ? freqHuman(x.value) : humanPlanText(x.to_text) };
        return { label: 'дни', from: Array.isArray(x.from) ? patternHuman(x.from) : humanPlanText(x.from_text),
            to: Array.isArray(x.value) ? patternHuman(x.value) : humanPlanText(x.to_text) };
    }

    function decRow(x, i) {
        const word = x.stale ? { cls: 'is-warn', icon: 'fa-triangle-exclamation', text: 'устарело' }
            : (x.kind === 'remove' ? (x.status === 'accepted' ? REMOVE_ROW_STATUS.accepted : REMOVE_ROW_STATUS.rejected)
                : (x.kind === 'transfer' ? (x.status === 'accepted' ? TRANSFER_ROW_STATUS.accepted : TRANSFER_ROW_STATUS.rejected)
                    : (x.status === 'accepted' ? ROW_STATUS.accepted : ROW_STATUS.rejected)));
        const name = decName(x), ch = decChange(x);
        return h('li', { class: 'ro-item ro-dec-row' + (x.stale ? ' is-stale' : (x.status === 'rejected' ? ' is-rejected' : ' is-accepted')) },
            h('div', { class: 'ro-item-main' },
                h('div', { class: 'ro-item-name' }, h('span', { class: 'n', text: name }),
                    h('span', { class: 'c', text: [x.customer_code, x.agent_code || x.agent_name].filter(Boolean).map(String).join(' · ') })),
                h('div', { class: 'ro-item-change' }, h('span', { class: 'lbl', text: ch.label + ': ' }),
                    h('span', { class: 'from', text: ch.from }), h('span', { class: 'arr', 'aria-hidden': 'true', text: ' → ' }),
                    h('span', { class: 'rt-sr-only', text: ', станет: ' }), h('span', { class: 'to', text: ch.to })),
                x.stale ? h('div', { class: 'ro-item-note is-warn', text: 'Сейчас в ERP: ' + humanPlanText(x.current_text) + ' — решение не применяется.' }) : null,
                x.manager_included === false ? h('div', { class: 'ro-item-note', text: 'Менеджер не в расчёте — в план для ERP не войдёт.' }) : null),
            h('div', { class: 'ro-item-side' },
                h('span', { class: 'ro-pill ' + word.cls }, icon(word.icon), word.text),
                h('div', { class: 'ro-item-acts' },
                    h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', dataset: { i: String(i) },
                        'aria-disabled': state.decBusy ? 'true' : null, 'aria-label': 'Отменить решение: ' + name },
                    icon('fa-rotate-left'), 'Отменить'))));
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
            announce('Решение отменено: ' + decName(x));
        } catch (e) {
            announce('Не удалось отменить решение: ' + sentence(whyText(e)));
            $('roDecErr').textContent = 'Не удалось отменить решение по «' + decName(x) + '»: ' + whyText(e) + '.';
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
        if (!window.confirm('Сбросить все решения (' + n + ')?\n\nПринятые и оставленные предложения снова станут «пока не решено», '
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
        b.querySelector('span').textContent = state.mapTransfers ? 'Скрыть передачи' : 'Показать передачи';
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
            const mk = L.marker(at, { icon: pin('rt-pin-transfer', color, 'fa-people-arrows', 24), title: 'Передать: ' + custName(c.customer_id), zIndexOffset: 800 })
                .bindTooltip(() => h('div', {}, h('b', { text: custName(c.customer_id) }), h('br'),
                    h('span', { style: 'color:#a7b0c0', text: 'передать: ' + agentName(c.from_agent) + ' → ' + agentName(c.to_agent) })), { direction: 'top', offset: [0, -10] })
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
            box.append(h('p', { class: 'ro-muted mb-0', text: m ? 'В этом плане у менеджера нет дней с визитами.' : 'Нет менеджеров.' }));
            renderDayInfo();
            return;
        }
        const W = cycleOf(m);
        for (let w = 1; w <= W; w++) {
            const list = days.filter(d => weekOf(d) === w).sort((a, b) => num(a.weekday) - num(b.weekday));
            if (!list.length) continue;
            const grid = h('div', { class: 'ro-daylist', role: 'group', 'aria-label': W > 1 ? w + '-я неделя' : 'Дни недели' },
                list.map(d => dayButton(d, W)));
            box.append(W > 1 ? h('div', { class: 'ro-weekrow' }, h('span', { class: 'lbl', 'aria-hidden': 'true', text: w + '-я неделя' }), grid) : grid);
        }
        renderDayInfo();
    }

    function dayButton(d, W) {
        const wd = num(d.weekday), w = weekOf(d), visits = num(d.visits) || 0;
        const full = (WD_FULL[wd] || 'день ' + wd) + (W > 1 ? ', ' + w + '-я неделя' : '');
        return h('button', { type: 'button', class: 'ro-dayb' + (w > 1 ? ' is-w2' : ''), style: '--wd:' + wdColor(wd),
            'aria-pressed': String(state.mapDay === dayKey(d)), dataset: { key: dayKey(d) },
            'aria-label': cap(full) + ': ' + fmt(visits) + ' ' + plural(visits, 'визит', 'визита', 'визитов') + ', ' + fmt(d.manager_km, 1) + ' км' },
            h('span', { class: 'd', text: WD_SHORT[wd] || String(wd) }),
            h('span', { class: 's', text: fmt(visits) + ' ' + plural(visits, 'визит', 'визита', 'визитов') + ' · ' + fmt(d.manager_km, 0) + ' км' }));
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
            h('div', { class: 'ro-dayinfo-t', text: cap(WD_FULL[wd] || 'день ' + wd) + (W > 1 ? ', ' + weekOf(d) + '-я неделя' : '')
                + (state.mapMode === 'before' ? ' — сейчас' : ' — если принять') }),
            h('div', { text: fmt(visits) + ' ' + plural(visits, 'визит', 'визита', 'визитов') + ' · ' + fmt(d.manager_km, 1) + NB + 'км · весь день ' + hm(d.plan_minutes) }),
            num(d.p_day_ge_min) !== null ? h('div', { text: 'шанс набрать ' + minText() + ' зимой — ' + pct(d.p_day_ge_min) }) : null,
            noGeo ? h('div', { class: 'ro-warnline' }, icon('fa-location-crosshairs'),
                fmt(noGeo) + ' ' + plural(noGeo, 'магазин', 'магазина', 'магазинов') + ' без координат — на карте их нет') : null,
            homeOf(m) ? null : h('div', { class: 'ro-warnline' }, icon('fa-house'), 'дом неизвестен — линия от первого магазина до последнего'),
            h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm mt-2', dataset: { all: '1' } }, icon('fa-xmark'), 'Показать все дни'),
        ].filter(Boolean));   // append(null) вставил бы текст «null»
    }

    function updateMapNote(m) {
        const note = $('roMapNote');
        if (!m) { note.textContent = ''; return; }
        const ids = new Set(), noGeo = new Set();
        modeDays(m).forEach(d => d.stops.forEach(st => { (stopGeo(st) ? ids : noGeo).add(String(st.customer_id)); }));
        noGeo.forEach(id => { if (ids.has(id)) noGeo.delete(id); });
        note.textContent = 'на карте ' + fmt(ids.size) + ' ' + plural(ids.size, 'магазин', 'магазина', 'магазинов')
            + (noGeo.size ? ' · без координат ' + fmt(noGeo.size) : '');
    }

    function renderMapLegend(m) {
        const box = $('roMapLegend');
        box.textContent = '';
        if (!m) return;
        const W = cycleOf(m);
        const wds = [...new Set(modeDays(m).map(d => num(d.weekday)))].sort((a, b) => a - b);
        const item = (sw, text) => h('span', { class: 'rt-lg rt-lg-static' }, sw, text);
        box.append(h('span', { class: 'rt-lg-cap', text: 'Цвет точки — день недели:' }));
        wds.forEach(wd => box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:' + wdColor(wd), 'aria-hidden': 'true' }),
            WD_SHORT[wd] || String(wd))));
        if (W > 1) {
            box.append(item(h('span', { class: 'ro-lg-sw', style: 'background:#a7b0c0', 'aria-hidden': 'true' }), 'каждую неделю или в 1-ю'));
            box.append(item(h('span', { class: 'ro-lg-sw is-hollow', style: 'color:#a7b0c0', 'aria-hidden': 'true' }), 'только во 2-ю неделю — пунктир'));
        }
        box.append(item(h('span', { class: 'rt-lg-ico', style: 'border:1.5px solid #a7b0c0;border-radius:50%', 'aria-hidden': 'true' }, icon('fa-house')), 'дом менеджера'));
        if (depotOf()) box.append(item(h('span', { class: 'rt-lg-ico', style: 'background:#eef1f6;color:#0c0f14', 'aria-hidden': 'true' }, icon('fa-warehouse')), 'склад'));
        if (mapTransfersOf(m).length) box.append(item(h('span', { class: 'rt-lg-ico', style: 'border:1.5px solid #a7b0c0;border-radius:50%', 'aria-hidden': 'true' },
            icon('fa-people-arrows')), 'передать — стрелка к дому того, кому передают'));
    }

    // Дни магазина в плане режима словами: «пн и чт каждую неделю»
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
        const word = ch ? (isRemove(ch) ? REMOVE_WORD : (isTransfer(ch) ? TRANSFER_WORD : DECISION_WORD))[statusOf(ch)] || DECISION_WORD.none : 'изменений нет';
        return h('div', { class: 'rt-pop' },
            h('div', { class: 'rt-pop-t', text: custName(cid) }),
            c.code ? h('div', { class: 'rt-pop-s', text: String(c.code) }) : null,
            row('Сейчас', ch ? patternHuman(ch.from.pattern) : visitsHuman(m, 'before', cid)),
            row('Если принять', ch ? (isRemove(ch) ? 'не посещать' : patternHuman(ch.to.pattern)) : visitsHuman(m, 'after', cid)),
            ch ? h('div', { class: 'rt-pop-note', text: rowReason(ch) }) : null,
            silent ? row('Покупки', silent) : (rate ? row('Заказы', rate) : null),
            row('Решение', word));
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
                L.marker(home, { icon: pin('rt-pin-home', m._color, 'fa-house', 24), title: 'Дом: ' + mgrName(m), zIndexOffset: 500 })
                    .bindTooltip(() => h('div', {}, h('b', { text: 'Дом' }), ' · ' + mgrName(m)), { direction: 'top', offset: [0, -10] })
                    .addTo(layers.base);
                bounds.push(home);
            }
        }
        drawTransfers(m, bounds);
        const dp = depotOf();
        if (dp) {
            L.marker(dp, { icon: pin('rt-pin-depot', null, 'fa-warehouse', 28), title: 'Склад', zIndexOffset: 1000 })
                .bindTooltip(() => h('div', {}, h('b', { text: 'Склад' }), ' — отсюда выезжают машины'), { direction: 'top', offset: [0, -12] })
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
        announce('На карте: ' + mgrName(m));
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
            status = state.decError ? 'Сколько изменений войдёт в файл, узнать не удалось — его всё равно можно скачать.' : '';
        } else if (n) {
            status = 'В файл ' + plural(n, 'войдёт', 'войдут', 'войдут') + ' ' + fmt(n) + ' '
                + plural(n, 'принятое изменение', 'принятых изменения', 'принятых изменений') + '.';
        } else {
            status = 'Пока нечего выгружать: примите хотя бы одно предложение.';
        }
        note.textContent = '';
        note.append('В файле — новый план по менеджерам и дням; внесите его в ERP вручную. ',
            status ? h('span', { class: n === 0 ? 'is-warn' : 'is-ok', text: status }) : null);
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
        const MARK = { move: 'перенос', frequency: 'частота', freq: 'частота', both: 'перенос, частота', remove: 'убрать', transfer: 'передать' };
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

    // Текст шаблона для Excel: с сервера, иначе — из пар [неделя, день]: «вт, каждую неделю»
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
        return wds.map((wd, i) => names[i] + ' (неделя ' + sets[i].replace(/,/g, ' и ') + ')').join(', ');
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
                    ? 'передать: ' + pick(x, 'from_agent_code') + ' → ' + pick(x, 'to_agent_code') : (TYPE_TEXT[type] || markText(type)),
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
                x.agent_code ? ' (' + x.agent_code + ')' : '', ': решено «' + humanPlanText(x.to_text) + '», сейчас в ERP — «'
                + humanPlanText(x.current_text) + '»')));
            if (stale.length > STALE_LIST_MAX) ul.append(h('li', { text: 'и ещё ' + fmt(stale.length - STALE_LIST_MAX) }));
            lines.push(h('span', {}, h('b', { text: 'Не вошли ' + fmt(stale.length) + ' ' + plural(stale.length, 'устаревшее решение', 'устаревших решения', 'устаревших решений') }),
                ' — план этих магазинов в ERP изменился после решения.', ul));
        }
        const mm = dataMismatch(d.data_as_of);
        if (mm) lines.push(h('span', {}, 'План для ERP собран по данным ERP на ' + stamp(mm.now) + ', а расчёт на экране — на '
            + stamp(mm.calc) + '. Принятые изменения применены к текущему плану ERP; чтобы предложения совпали с ним, пересчитайте.'));
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
            box.append(h('span', { class: 'ro-muted', text: 'Список менеджеров не загрузился — расчёт пойдёт по менеджерам «в расчёте» из настроек.' }));
            const err = $('roMgrsErr');
            err.textContent = '';
            const again = h('button', { type: 'button', class: 'rt-linkbtn', text: 'Загрузить снова' });
            again.addEventListener('click', () => {
                box.textContent = '';
                box.append(h('span', { class: 'ro-muted' }, spin(), ' Загружаю список менеджеров…'));
                loadRefs();
            });
            err.append(h('span', { text: e.network ? 'Нет связи с сервером.' : sentence(e.message || 'Ошибка сервера.') }), again);
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
                announce('Отметьте хотя бы одного менеджера');
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
            if ($('roRecalcBtn').getAttribute('aria-disabled') === 'true') { announce('Расчёт уже идёт — дождитесь окончания'); return; }
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
            announce(state.mapTransfers ? 'На карте — передачи магазинов' : 'Передачи скрыты');
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
            if ($('roDecReset').getAttribute('aria-disabled') === 'true') { announce('Решений нет — сбрасывать нечего'); return; }
            resetAll();
        });

        loadRefs();
        loadLast(false);
        resumeJob();
    });
})();

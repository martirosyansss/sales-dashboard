/* «Развоз» /routes/dispatch — план развоза на завтра (docs/plans/dispatch-plan.md).
   Данные: GET /api/routes/dispatch?date=…; «Собрать рейсы» — POST /api/routes/dispatch/build;
   правки логиста — POST /api/routes/dispatch/edit (move | pin | unpin | exclude | include, с номером
   черновика rev); «Начать заново» — POST /api/routes/dispatch/reset; ручная точка магазина —
   POST /api/routes/geo-override; «План и факт» — GET /api/routes/dispatch/fact?date=…; окно приёма магазина
   («Ընդունման ժամ», windows-center-plan.md) — POST /api/routes/customer-window. Точка любого магазина «Փոխել տեղը»
   — тот же POST /api/routes/geo-override (null — «авто»); предложения водителей (geo_suggestions в ответе дня,
   driver-geo-plan.md §5) — POST /api/routes/geo-suggest/decide. Рейсы после смены точки сами не пересобираются.
   Раз в 5 минут, пока страница открыта, — GET /api/routes/dispatch/status?date=…: «заказы ещё поступают»
   и сколько заказов пришло или ушло с последней сборки. Рейсы сами не пересобираются — только по кнопке.
   Безопасность: всё, что пришло из ERP (магазины, адреса, менеджеры, машины), выводится только через
   esc() или textContent — в том числе в попапах карты, листах для водителей и Excel. POST — только JSON.
   Язык страницы — армянский (ответ владельца №31). Ошибки сервера приходят по-русски — переводятся
   словарём SERVER_HY; незнакомый текст показывается как есть.
   Интерфейс «для чайников»: вверху «Ի՞նչ անել հիմա» — одна подсказка и главная кнопка по состоянию дня;
   рейсы — простой список, как лист водителя; правки — по кнопке «Փոփոխել» у рейса.
   Раскладка (редизайн, ответ владельца №51): шаги 1–2 после сборки свёрнуты в строку; шкала дня — рейсы машин
   по часам; карточки машин раскрываются по нажатию; карта справа от рейсов. Нажатие на машину или рейс на шкале —
   он же на карте и раскрытая карточка.
   Под картой — «почему так» про выбранное над ней (ответ владельца №49): рейс, машина или весь день — по цифрам
   explain из ответа дня (dispatch.plan_view), без утверждений, которых расчёт не делает.
   «Հարցրու AI-ին» (ответ владельца №52) — POST /api/routes/dispatch/ask: вопрос логиста по дню, история разговора
   хранится здесь (по дням), сервер отвечает по тем же цифрам дня и ничего не меняет. Ответ AI выводится только
   через textContent (абзацы и строки «• » — без разметки). */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    // Формат чисел как в Армении: пробел между разрядами, запятая в дроби (у 'ru-RU' так же, а данных 'hy-AM' в браузере может не быть)
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const NB = ' ';
    const kgText = (kg) => { const n = num(kg) || 0; return n >= 1000 ? fmt(n / 1000, 1) + NB + 'տ' : fmt(n) + NB + 'կգ'; };
    const money = (v) => fmt(Math.round(num(v) || 0)) + NB + 'դրամ';
    // По-армянски существительное после числа — в единственном числе: «5 պատվեր», «3 խանութ»
    const pl = (n, word) => fmt(n) + NB + word;
    const WD_NAME = { 1: 'երկուշաբթի', 2: 'երեքշաբթի', 3: 'չորեքշաբթի', 4: 'հինգշաբթի', 5: 'ուրբաթ', 6: 'շաբաթ', 7: 'կիրակի' };
    const WD_SHORT = { 1: 'երկ', 2: 'երք', 3: 'չրք', 4: 'հնգ', 5: 'ուրբ', 6: 'շբթ', 7: 'կիր' };
    // Окно приёма магазина (№35–38): минуты от полуночи → «մինչև 12:00», «14:00-ից հետո», «10:00–14:00», «11:00 ±15»
    const hhmm = (m) => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
    const isMin = (v) => Number.isInteger(v) && v >= 0 && v < 24 * 60;
    function windowText(w) {
        if (!isObj(w) || !isMin(w.t1)) return '';
        if (w.kind === 'before') return 'մինչև ' + hhmm(w.t1);
        if (w.kind === 'after') return hhmm(w.t1) + '-ից հետո';
        if (w.kind === 'between' && isMin(w.t2)) return hhmm(w.t1) + '–' + hhmm(w.t2);
        if (w.kind === 'at' && Number.isInteger(w.tol)) return hhmm(w.t1) + (w.tol ? ' ±' + w.tol : '');
        return '';
    }
    function vehicleAllowed(stop, code) {
        const rule = stop.vehicle_access;
        return !rule || (rule.mode === 'allow' ? rule.trucks.includes(code) : !rule.trucks.includes(code));
    }
    function vehicleText(rule, compact = false) {
        if (!rule) return '';
        if (rule.mode === 'allow' && !rule.trucks.length) return 'Ոչ մի մեքենա թույլատրված չէ';
        if (rule.mode === 'deny' && !rule.trucks.length) return 'Բոլոր մեքենաները';
        const codes = compact ? rule.trucks.slice(0, 2) : rule.trucks;
        return (rule.mode === 'allow' ? 'Միայն՝ ' : 'Չեն կարող՝ ') + codes.join(', ')
            + (compact && rule.trucks.length > 2 ? ' · ևս ' + (rule.trucks.length - 2) : '');
    }
    const dateRu = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    const wdOf = (s) => { const d = new Date(s + 'T12:00:00'); return Number.isNaN(d.getTime()) ? null : (d.getDay() || 7); };
    // Цвета машин: тот же ряд, что у менеджеров в «Обзоре» (различимы и при дальтонизме)
    const COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3', '#0d9298', '#b8b90c', '#ae55c1', '#37981b'];
    const YEREVAN = [40.1792, 44.4991];
    const POLL_MS = 5 * 60 * 1000;          // автообновление заказов дня
    const MANAGERS_DONE = '16:40';          // владелец: менеджеры заканчивают день ≈ 16:40

    const state = {
        day: null, data: null, busy: false,
        map: null, layers: null, mapFailed: false, mapBounds: null,
        mapFitting: false, mapUserMoved: false,   // логист сам двигал или приближал карту — не перевписывать
        mapFocus: null,                     // карта только одной машины {truck} или одного рейса {truck, trip}; null — все
        roadCache: new Map(), roadGen: 0,   // линии рейсов по дорогам: ключ — точки линии; номер отрисовки
        pickMap: null, pickMarker: null, pickCid: null,
        loadSeq: 0,                         // номер последнего запроса дня: ответы на прежние запросы не применяются
        editing: new Set(),                 // рейсы, открытые кнопкой «Փոփոխել»
        fetchedAt: 0,                       // когда последний раз спрашивали сервер о заказах дня
        geoMap: null, geoMarker: null, geoStop: null,   // «Փոխել տեղը»: карта диалога и магазин
        sugMap: null, sugLayer: null, sugSel: null,     // предложения водителей: карта и выбранное (event_id)
        geoChanged: null,                   // день, в котором после сборки меняли точку магазина — подсказать пересборку
        unloadStop: null, unloadInfo: null, unloadSeq: 0,   // «Ժամանակ խանութում»: магазин диалога, его данные с сервера, номер запроса
        stepsOpen: new Set(),               // шаги 1–2, раскрытые логистом после сборки (иначе свёрнуты в строку)
        open: new Set(),                    // раскрытые карточки машин (код машины)
        ai: { chats: new Map(), busy: false, shownDay: null },   // «Հարցրու AI-ին»: разговор по каждому дню [{role, text}]
    };

    // ---------- Сервер ----------
    const HTTP_TEXT = {
        400: 'Սերվերը չընդունեց հարցումը։', 401: 'Անհրաժեշտ է մուտք գործել համակարգ։', 403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։',
        404: 'Չի գտնվել։', 409: 'Պլանը փոխել են մեկ այլ ներդիրում — թարմացրեք էջը։', 415: 'Սերվերը չընդունեց հարցումը։',
        500: 'Սերվերի ներքին սխալ։', 503: 'ERP տվյալների բազան հասանելի չէ։',
    };
    // Тексты ошибок сервера (views.py, dispatch.py) → армянский. Точное совпадение; незнакомое — как есть.
    const BAD_REQUEST = 'Սերվերը չընդունեց հարցումը';
    const SERVER_HY = {
        'База данных ERP недоступна': 'ERP տվյալների բազան հասանելի չէ',
        'Внутренняя ошибка': 'Սերվերի ներքին սխալ',
        'Ожидается JSON (Content-Type: application/json)': BAD_REQUEST,
        'Некорректный JSON': BAD_REQUEST,
        'ожидался JSON-объект': BAD_REQUEST,
        'ожидалось {"customer_id", "lat", "lon"}': BAD_REQUEST,
        'дата в формате ГГГГ-ММ-ДД': 'Ամսաթիվը պետք է լինի ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով',
        'прошедшая дата в формате ГГГГ-ММ-ДД': 'Պետք է անցած ամսաթիվ՝ ՏՏՏՏ-ԱԱ-ՕՕ ձևաչափով',
        'ожидался список машин': 'Սպասվում էր մեքենաների ցուցակ',
        'отметьте хотя бы одну машину': 'Նշեք գոնե մեկ մեքենա',
        'Сначала укажите склад и тоннаж с расходом машин в настройках': 'Նախ կարգավորումներում նշեք պահեստը և մեքենաների տոննաժն ու ծախսը',
        'Сначала соберите рейсы': 'Նախ կազմեք երթերը',
        'Заказы этого рейса слишком давние для переноса на завтра — решите сегодня': 'Այս երթի պատվերները շատ հին են վաղվան տեղափոխելու համար — որոշեք այսօր',
        'Прошедший день — перенос на другой день не меняется': 'Անցած օր է — այլ օր տեղափոխումը չի փոխվում',
        'План изменили в другой вкладке — обновите страницу': 'Պլանը փոխել են մեկ այլ ներդիրում — թարմացրեք էջը',
        'Рейс не найден — обновите страницу': 'Երթը չի գտնվել — թարմացրեք էջը',
        'Заказ не найден среди заказов дня — обновите страницу': 'Պատվերը չի գտնվել օրվա պատվերների մեջ — թարմացրեք էջը',
        'Эта машина сегодня не работает — отметьте её в шаге 1': 'Այս մեքենան այսօր չի աշխատում — նշեք այն 1-ին քայլում',
        'Неизвестное действие': 'Անհայտ գործողություն',
        'Точка не найдена — обновите страницу': 'Կետը չի գտնվել — թարմացրեք էջը',
        'Точка уже в рейсе — укажите, из какого': 'Կետն արդեն երթում է — նշեք, թե որ երթից',
        'Этой точки нет в рейсе — обновите страницу': 'Այս կետը երթում չէ — թարմացրեք էջը',
        'ожидался код клиента': 'Սպասվում էր հաճախորդի կոդ',
        'точка вне Армении': 'Կետը Հայաստանից դուրս է',
        // окно приёма клиента — POST /api/routes/customer-window (store.check_window)
        'ожидалось {"customer_id", "window"}': BAD_REQUEST,
        'ожидалось {"kind", "t1", "t2", "tol"}': BAD_REQUEST,
        'лишнее поле у этого вида окна': BAD_REQUEST,
        'вид окна: before, after, between или at': 'Ընդունման ժամի տեսակը սխալ է',
        'время окна — минуты от 0 до 1439': 'Ժամը պետք է լինի 00:00-ից մինչև 23:59',
        'конец интервала должен быть позже начала': 'Միջակայքի վերջը պետք է լինի սկզբից ուշ',
        'допуск — от 0 до 120 минут': 'Թույլատրելի շեղումը՝ 0-ից մինչև 120 րոպե',
        // предложения водителей — POST /api/routes/geo-suggest/decide
        'ожидалось {"event_id", "decision"}': BAD_REQUEST,
        'ожидался id предложения': BAD_REQUEST,
        'решение: accepted или rejected': BAD_REQUEST,
        'Предложение не найдено или уже решено — обновите страницу': 'Առաջարկը չի գտնվել կամ արդեն որոշված է — թարմացրեք էջը',
        // «Հարցրու AI-ին» — POST /api/routes/dispatch/ask (ai_chat.py)
        'вопрос — непустой текст до 1000 символов': 'Հարցը պետք է լինի ոչ դատարկ՝ մինչև 1000 նիշ',
        'история диалога: ожидался список реплик': BAD_REQUEST,
        'AI недоступен: не задан ANTHROPIC_API_KEY в .env сервера': 'AI-ն միացված չէ՝ սերվերում ANTHROPIC_API_KEY բանալին նշված չէ',
        'AI недоступен: ключ ANTHROPIC_API_KEY не принят': 'AI-ն հասանելի չէ՝ ANTHROPIC_API_KEY բանալին չի ընդունվել',
        'AI недоступен: модель не найдена — проверьте ROUTES_AI_MODEL': 'AI-ն հասանելի չէ՝ մոդելը չի գտնվել (ROUTES_AI_MODEL)',
        'AI сейчас перегружен — повторите через минуту': 'AI-ն այժմ ծանրաբեռնված է — կրկնեք մեկ րոպեից',
        'AI не принял запрос — начните новый разговор': 'AI-ն չընդունեց հարցումը — սկսեք նոր զրույց',
        'AI временно недоступен — повторите позже': 'AI-ն ժամանակավորապես հասանելի չէ — կրկնեք ավելի ուշ',
        'Нет связи с AI — повторите позже': 'AI-ի հետ կապ չկա — կրկնեք ավելի ուշ',
        'AI не дал ответа — повторите вопрос': 'AI-ն պատասխան չտվեց — կրկնեք հարցը',
        'AI недоступен: на счёте Anthropic закончились средства — пополните баланс': 'AI-ն հասանելի չէ՝ Anthropic-ի հաշվին միջոցները վերջացել են — լիցքավորեք հաշիվը',
        'AI не успел ответить — спросите короче или повторите': 'AI-ն չհասցրեց պատասխանել — հարցրեք ավելի կարճ կամ կրկնեք',
        // «Ժամանակ խանութում» — POST /api/routes/customer-vehicles {customer_id, unload_min} (store.check_unload_min)
        'ожидалось {"customer_id", "access"} с необязательными "window" и "unload_min" или {"customer_id", "unload_min"}': BAD_REQUEST,
        'магазин не найден — обновите страницу': 'Խանութը չի գտնվել — թարմացրեք էջը',
        'время у магазина — целое число минут от 1 до 120': 'Ժամանակ խանութում՝ ամբողջ թիվ 1-ից մինչև 120 րոպե',
    };
    const SERVER_HY_PREFIX = [['машина не готова к расчёту: ', 'Մեքենան պատրաստ չէ հաշվարկի համար՝ ']];
    // StoreError «База настроек маршрутов <файл>: не удалось сохранить …» — по окончанию текста
    const SERVER_HY_SUFFIX = [
        [': не удалось сохранить окно приёма клиента', 'Չհաջողվեց պահպանել ընդունման ժամը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить точку клиента', 'Չհաջողվեց պահպանել խանութի կետը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить план развоза', 'Չհաջողվեց պահպանել առաքման պլանը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить время у магазина', 'Չհաջողվեց պահպանել ժամանակը խանութում — կարգավորումների բազան հասանելի չէ'],
    ];
    function serverText(s) {
        const t = String(s).trim();
        if (Object.prototype.hasOwnProperty.call(SERVER_HY, t)) return SERVER_HY[t];
        const sfx = SERVER_HY_SUFFIX.find(([ru]) => t.endsWith(ru));
        if (sfx) return sfx[1];
        const p = SERVER_HY_PREFIX.find(([ru]) => t.startsWith(ru));
        return p ? p[1] + t.slice(p[0].length) : t;
    }
    // timeoutMs — оборвать ожидание (вопрос AI: сервер укладывается в 100 с, страница ждёт 150 с)
    async function api(method, url, body, timeoutMs) {
        const opts = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        let timer = null;
        if (timeoutMs && typeof AbortController !== 'undefined') {
            const ctl = new AbortController();
            opts.signal = ctl.signal;
            timer = setTimeout(() => ctl.abort(), timeoutMs);
        }
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let resp, data = null;
        try {
            try { resp = await fetch(url, opts); } catch (e) {
                if (e && e.name === 'AbortError') throw Object.assign(new Error('Պատասխանը շատ երկար է սպասվում — կրկնեք ավելի ուշ։'), { status: 0, data: null });
                throw Object.assign(new Error('Սերվերի հետ կապ չկա։'), { status: 0, data: null });
            }
            try { data = await resp.json(); } catch (e) { data = null; }     // и чтение тела — под тем же таймаутом
        } finally { if (timer) clearTimeout(timer); }
        if (resp.ok && isObj(data) && data.success === true) return data;
        const d = isObj(data) ? data : null;
        const msg = (d && typeof d.error === 'string' && d.error.trim() ? serverText(d.error) : '')
            || (d && isObj(d.errors) ? Object.values(d.errors).map(serverText).join('; ') : '')
            || HTTP_TEXT[resp.status] || ('Սերվերի սխալ (կոդ ' + resp.status + ')։');
        throw Object.assign(new Error(msg), { status: resp.status, data: d });
    }

    function announce(msg) {
        const el = $('dpStatus');
        el.textContent = '';
        setTimeout(() => { el.textContent = msg; }, 40);
    }
    let toastTimer = null;
    function toast(msg) {
        let el = $('dpToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'dpToast';
            el.className = 'dp-toast';
            el.setAttribute('role', 'status');
            $('rtDispatch').appendChild(el);
        }
        el.textContent = msg;
        el.classList.add('is-on');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('is-on'), 4200);
        announce(msg);
    }
    function showActionError(err) {
        $('dpActionErrorText').textContent = err.message || String(err);
        if (state.data && /1-ին քայլում/.test(err.message || '')) unfoldStep('dpStep1');   // «…1-ին քայլում» — раскрыть шаг 1, чтобы было где отметить
        $('dpActionReload').classList.toggle('d-none', !(err.status === 409));
        $('dpActionError').classList.remove('d-none');
        $('dpActionError').focus();
    }
    const hideActionError = () => $('dpActionError').classList.add('d-none');

    // ---------- Загрузка ----------
    async function load(day, refresh) {
        const seq = ++state.loadSeq;
        $('dpLoading').hidden = false;
        $('dpLoading').classList.remove('d-none');
        $('dpLoadError').classList.add('d-none');
        try {
            const q = new URLSearchParams();
            if (day) q.set('date', day);
            if (refresh) q.set('refresh', '1');
            const data = await api('GET', '/api/routes/dispatch' + (q.toString() ? '?' + q : ''));
            if (seq !== state.loadSeq) return;
            $('dpBody').hidden = false;     // до отрисовки: карте Leaflet нужен настоящий размер блока, у скрытого он 0×0
            setData(data);
        } catch (e) {
            if (seq !== state.loadSeq) return;
            $('dpLoadErrorText').textContent = e.message;
            $('dpLoadError').classList.remove('d-none');
        } finally {
            if (seq === state.loadSeq) $('dpLoading').classList.add('d-none');
        }
    }
    // Смена дня кнопками и календарём — не посреди сборки или правки (их ответ вернул бы прежний день)
    function goDay(day) {
        if (state.busy) { if (state.day) $('dpDate').value = state.day; return; }
        load(day);
    }

    function setData(data) {
        const trips = new Set();
        if (data.plan) data.plan.trucks.forEach(t => t.trips.forEach(tr => trips.add(tr.id)));
        const hadPlan = !!(state.data && state.data.day === data.day && state.data.plan);
        if (data.day !== state.day) { state.mapFocus = null; state.stepsOpen.clear(); }
        // рейсы дня появились: до трёх машин — все раскрыты, больше — первая (остальные по нажатию, день виден целиком)
        if (data.plan && !hadPlan) {
            const codes = data.plan.trucks.map(t => t.car_code);
            state.open = new Set(codes.length <= 3 ? codes : codes.slice(0, 1));
        }
        if (data.day !== state.day || !data.plan) state.editing.clear();
        else [...state.editing].forEach(id => { if (!trips.has(id)) state.editing.delete(id); });
        state.data = data;
        state.day = data.day;
        // кнопка AI — когда день загружен; открытая панель другого дня — показать разговор этого дня
        if ($('dpAiOpen')) { $('dpAiOpen').hidden = !$('dpAi').hidden; if (!$('dpAi').hidden && state.ai.shownDay !== data.day) aiRender(); }
        state.fetchedAt = Date.now();
        const url = new URL(window.location.href);
        url.searchParams.set('date', data.day);
        window.history.replaceState(null, '', url);
        render();
    }

    // ---------- Даты по-человечески ----------
    const MONTH_GEN = ['հունվարի', 'փետրվարի', 'մարտի', 'ապրիլի', 'մայիսի', 'հունիսի', 'հուլիսի', 'օգոստոսի', 'սեպտեմբերի', 'հոկտեմբերի', 'նոյեմբերի', 'դեկտեմբերի'];
    const isDay = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);
    function shiftDay(s, n) {
        const d = new Date(s + 'T12:00:00');
        d.setDate(d.getDate() + n);
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    // «2 հոկտեմբերի»; withWd — «ուրբաթ, 2 հոկտեմբերի»
    function dayHuman(s, withWd) {
        if (!isDay(s)) return '—';
        const t = Number(s.slice(8, 10)) + ' ' + MONTH_GEN[Number(s.slice(5, 7)) - 1];
        return withWd ? (WD_NAME[wdOf(s)] || '') + ', ' + t : t;
    }
    // «Այսօր» / «Վաղը» / «Երեկ» относительно сегодняшнего дня сервера
    function relDay(s) {
        const today = state.data && state.data.today;
        if (!isDay(today) || !isDay(s)) return '';
        if (s === today) return 'Այսօր';
        if (s === shiftDay(today, 1)) return 'Վաղը';
        if (s === shiftDay(today, -1)) return 'Երեկ';
        return '';
    }

    // ---------- Отрисовка ----------
    const MONTH_SHORT = ['հնվ', 'փտվ', 'մրտ', 'ապր', 'մյս', 'հնս', 'հլս', 'օգս', 'սեպ', 'հոկ', 'նոյ', 'դեկ'];
    function render() {
        const d = state.data;
        const rel = relDay(d.day);
        $('dpTitle').textContent = (rel ? rel + '՝ ' : '') + dayHuman(d.day, true);
        // листок календаря в шапке: день недели, число, месяц и год
        $('dpCalWd').textContent = WD_SHORT[wdOf(d.day)] || '—';
        $('dpCalDay').textContent = isDay(d.day) ? String(Number(d.day.slice(8, 10))) : '–';
        $('dpCalMon').textContent = isDay(d.day) ? MONTH_SHORT[Number(d.day.slice(5, 7)) - 1] + ' ' + d.day.slice(0, 4) : '';
        document.title = 'Առաքում ' + dateRu(d.day) + ' — Sales Dashboard';
        $('dpDate').value = d.day;
        $('dpTomorrow').hidden = d.day === d.default_day;
        renderDateNote();
        renderTrucks();
        renderOrders();
        renderNoCoords();
        renderGeoSug();
        renderOrderLists();
        const plan = d.plan;
        $('dpBuildText').textContent = plan ? 'Վերակազմել երթերը' : 'Կազմել երթերը';
        $('dpReset').hidden = !plan;
        $('dpBuild').disabled = !d.trucks.some(t => t.ready) || !d.depot;
        $('dpBuildNote').textContent = plan ? 'Ամրացված երթերը կմնան ինչպես կան, մնացածը ծրագիրը կբաշխի նորից։' : 'Մոտ 5 վայրկյան։';
        renderSteps();
        $('dpStep3').hidden = !plan;
        if (plan) renderPlan(plan);
        $('dpAnalysis').hidden = !plan && !d.is_past;
        $('dpFact').hidden = !d.is_past;
        if (!d.is_past) $('dpFactOut').textContent = '';
        renderFresh();
    }

    function renderDateNote() {
        const d = state.data, od = d.order_dates;
        const orders = od.since === od.until ? dayHuman(od.since, true) : dayHuman(od.since, true) + ' – ' + dayHuman(od.until, true);
        $('dpDateNote').textContent = 'Տանում ենք պատվերները, որոնք ընդունվել են՝ ' + orders
            + (d.data_as_of ? ' · ERP-ից կարդացել ենք ժամը ' + d.data_as_of.slice(11, 16) + '-ին' : '');
    }

    // ---------- «Что делать сейчас»: одна подсказка и главная кнопка по состоянию дня ----------
    function builtWhen(iso) {
        if (typeof iso !== 'string' || iso.length < 16) return '';
        const day = iso.slice(0, 10);
        return (day === state.data.today ? '' : dayHuman(day) + ' ') + 'ժամը ' + iso.slice(11, 16);
    }
    function planIssues(plan) {
        let n = plan.unassigned.length;
        plan.trucks.forEach(t => t.trips.forEach(tr => { if (tr.over_time || tr.over_capacity || tr.no_truck || tr.window_miss || tr.center_miss || tr.vehicle_miss) n++; }));
        return n;
    }
    // Логист снял или поставил галочку машины после сборки — рейсы ещё на старых машинах
    function trucksChanged() {
        const was = state.data.trucks.filter(t => t.selected).map(t => t.car_code).sort().join('|');
        return was !== selectedTrucks().sort().join('|');
    }
    const SAFE_LINK = /^\/(?!\/)[^\s\\]*$/;
    function renderFresh() {
        const d = state.data, plan = d.plan, box = $('dpTodo');
        const o = d.live_orders || d.orders;
        let tone = 'is-info', ico = 'fa-hand-point-right', title = '';
        const lines = [], btns = [];
        const coming = d.orders_still_coming ? 'Պատվերները դեռ ընդունվում են՝ մինչև ' + d.ready_time + '-ը։' : '';
        const n = d.new_since_build, r = d.removed_since_build;
        const gone = r && r.count ? pl(r.count, 'պատվեր') + ' չեղարկվել կամ արդեն առաքվել է — երթերից դրանք հանված են։' : '';
        const problems = d.problems || [];
        if (problems.length) {
            tone = 'is-warn'; ico = 'fa-gear';
            title = 'Նախ լրացրեք կարգավորումները';
            problems.forEach(p => lines.push((PROBLEM_HY[p.code] || serverText(p.text)) + '։'));
            const link = problems.find(p => typeof p.link === 'string' && SAFE_LINK.test(p.link));
            btns.push({ href: link ? link.link : '/routes/settings', text: 'Բացել կարգավորումները', ico: 'fa-gear' });
        } else if (!plan && !o.count) {
            title = 'Այս օրվա համար պատվերներ դեռ չկան';
            lines.push(coming ? coming + ' Էջն ինքն է թարմանում 5 րոպեն մեկ։' : 'Ընտրեք մեկ այլ օր վերևում։');
        } else if (!plan) {
            title = 'Ստուգեք մեքենաները և սեղմեք «Կազմել երթերը»';
            lines.push('Ծրագիրը կբաշխի ' + pl(o.count, 'պատվեր') + ' (' + kgText(o.kg) + ') մեքենաների միջև։ Կտևի մոտ 5 վայրկյան։');
            if (coming) lines.push(coming + ' Կարող եք կազմել հիմա և վերակազմել ' + d.ready_time + '-ից հետո։');
            btns.push({ act: build, text: 'Կազմել երթերը', ico: 'fa-truck-fast' });
        } else if (!plan.summary.trips) {
            tone = 'is-warn'; ico = 'fa-inbox';
            title = 'Երթերում ոչ մի խանութ չկա';
            lines.push('Բոլոր պատվերները հանված են առաքումից կամ դրանց տեղը քարտեզում նշված չէ։ Ստուգեք 2-րդ քայլը և վերակազմեք երթերը։');
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
            btns.push({ act: () => stepButton('dpStep2', '').click(), text: 'Բացել 2-րդ քայլը', ico: 'fa-arrow-up' });
        } else if (n && n.count) {
            tone = 'is-warn'; ico = 'fa-bell';
            title = 'Եկել է ' + pl(n.count, 'նոր պատվեր') + ' — վերակազմեք երթերը';
            lines.push((builtWhen(d.built_at) ? 'Երթերը կազմվել են ' + builtWhen(d.built_at) + '։ ' : '') + 'Նոր պատվերները (' + kgText(n.kg) + ') դեռ երթերում չեն։');
            if (gone) lines.push(gone);
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
        } else if (trucksChanged()) {
            tone = 'is-warn'; ico = 'fa-truck';
            title = 'Դուք փոխել եք մեքենաները — վերակազմեք երթերը';
            lines.push('Երթերը դեռ կազմված են նախկին մեքենաներով։');
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
        } else if (state.geoChanged === d.day) {
            tone = 'is-warn'; ico = 'fa-location-dot';
            title = 'Խանութի կետը փոխվել է';
            lines.push(GEO_REBUILD);
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
        } else if (planIssues(plan)) {
            tone = 'is-warn'; ico = 'fa-triangle-exclamation';
            title = 'Երթերը կազմված են, բայց ոչ ամեն ինչ է տեղավորվել';
            lines.push('Ստորև գրված է՝ ինչը չի տեղավորվել և ինչ անել։');
            if (gone) lines.push(gone);
            btns.push({ act: () => { const el = $('dpOverflow').firstElementChild || $('dpUnassigned').firstElementChild; if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); }, text: 'Ցույց տալ', ico: 'fa-arrow-down' });
            btns.push({ act: printSheets, text: 'Տպել վարորդների համար', ico: 'fa-print' });
        } else {
            tone = 'is-ok'; ico = 'fa-circle-check';
            title = 'Երթերը պատրաստ են — տպեք թերթիկները վարորդների համար';
            const when = builtWhen(d.built_at);
            const tail = coming ? coming + ' Եթե գան նոր պատվերներ, այստեղ կհայտնվի հիշեցում։' : '';
            if (when || tail) lines.push(((when ? 'Կազմվել են ' + when + '։ ' : '') + tail).trim());
            if (gone) lines.push(gone);
            btns.push({ act: printSheets, text: 'Տպել վարորդների համար', ico: 'fa-print' });
            btns.push({ act: exportExcel, text: 'Ներբեռնել Excel', ico: 'fa-file-excel' });
        }
        // магазины без точки на карте в рейсы не попадают — напоминаем, пока они есть
        if (plan && d.orders.no_coords && !problems.length) lines.push('Ուշադրություն՝ ' + pl(d.orders.no_coords, 'խանութի') + ' տեղը քարտեզում նշված չէ, դրանք երթերում չեն (տես 2-րդ քայլը)։');
        if (d.is_past) lines.unshift('Սա անցած օր է՝ դիտելու և համեմատելու համար։');
        box.className = 'dp-todo ' + tone;
        box.innerHTML = '<div class="dp-todo-ico" aria-hidden="true"><i class="fas ' + ico + '"></i></div>'
            + '<div class="dp-todo-body"><p class="dp-todo-k">Ի՞նչ անել հիմա</p><h2 class="dp-todo-t"></h2>'
            + '<div class="dp-todo-lines"></div><div class="dp-todo-btns"></div></div>';
        box.querySelector('.dp-todo-t').textContent = title;
        const lb = box.querySelector('.dp-todo-lines');
        lines.forEach(t => { const p = document.createElement('p'); p.textContent = t; lb.appendChild(p); });
        const bb = box.querySelector('.dp-todo-btns');
        btns.forEach((b, i) => {
            const el = document.createElement(b.href ? 'a' : 'button');
            el.className = 'rt-btn ' + (i ? 'rt-btn-ghost' : 'rt-btn-primary');
            el.innerHTML = '<i class="fas ' + b.ico + '" aria-hidden="true"></i><span></span>';
            el.lastChild.textContent = b.text;
            if (b.href) el.href = b.href;
            else { el.type = 'button'; el.addEventListener('click', b.act); }
            bb.appendChild(el);
        });
    }

    // Автообновление: раз в POLL_MS спрашиваем сервер о заказах дня (ERP только читается, по правилам кэша).
    // Заказы изменились — страница перечитывается целиком, но не посреди правки (выбор в списке, точка на карте):
    // тогда обновляются только подсказки, а страница — при следующей проверке.
    const interacting = () => {
        const a = document.activeElement;
        return state.pickCid !== null || $('dpGeoDlg').open || $('dpUnloadDlg').open
            || (!!a && $('dpBody').contains(a) && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName));
    };
    async function poll() {
        const d = state.data;
        if (!d || d.is_past || state.busy || document.hidden || Date.now() - state.fetchedAt < POLL_MS) return;
        state.fetchedAt = Date.now();
        const day = state.day;
        let s;
        try { s = await api('GET', '/api/routes/dispatch/status?date=' + encodeURIComponent(day)); } catch (e) { return; }
        if (state.day !== day || state.data !== d || state.busy) return;     // пока ждали, сменили дату или правили
        const before = d.new_since_build ? d.new_since_build.count : 0;
        if ((s.orders_sig !== d.orders_sig || s.rev !== d.rev) && !interacting()) {
            const picked = trucksChanged() ? selectedTrucks() : null;
            try { await reloadQuiet(); } catch (e) { return; }
            if (picked && state.day === day) {
                $('dpTrucks').querySelectorAll('input[type="checkbox"]:not(:disabled)').forEach(cb => { cb.checked = picked.includes(cb.value); });
                renderTruckCount();
                renderFresh();
            }
        } else {
            ['orders_still_coming', 'ready_time', 'built_at', 'new_since_build', 'removed_since_build', 'data_as_of'].forEach(k => { d[k] = s[k]; });
            d.live_orders = s.orders;
            renderDateNote();
            renderFresh();
        }
        const after = state.data.new_since_build ? state.data.new_since_build.count : 0;
        if (after > before) announce('Վերջին կազմումից հետո եկել է ' + pl(after, 'նոր պատվեր'));
    }

    const PROBLEM_HY = { no_depot: 'Նշեք պահեստը՝ որտեղից են մեկնում մեքենաները', no_trucks: 'Նշեք մեքենաների բեռնատարողությունը և ծախսը' };

    function truckLabel(t) {
        return (t.name ? t.name + ' · ' : '') + t.car_code;
    }
    function truckColor(code) {
        const list = state.data.trucks.filter(t => t.ready).map(t => t.car_code);
        const i = list.indexOf(code);
        return COLORS[i >= 0 ? i % COLORS.length : COLORS.length - 1];
    }
    const truckBy = (code) => state.data.trucks.find(t => t.car_code === code) || { car_code: code, name: null };

    function renderTrucks() {
        const box = $('dpTrucks');
        box.querySelectorAll('.dp-truck, .dp-truck-empty').forEach(x => x.remove());
        const ready = state.data.trucks.filter(t => t.ready);
        if (!ready.length) {
            const p = document.createElement('p');
            p.className = 'dp-truck-empty';
            p.innerHTML = 'Մեքենաները լրացված չեն։ <a href="/routes/settings#trucks">Լրացրեք դրանք կարգավորումներում →</a>';
            box.appendChild(p);
        }
        state.data.trucks.forEach(t => {
            const lab = document.createElement('label');
            lab.className = 'dp-truck' + (t.ready ? '' : ' is-off');
            if (t.ready) lab.style.setProperty('--dp-c', truckColor(t.car_code));
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = t.car_code;
            cb.checked = !!t.selected;
            cb.disabled = !t.ready;
            cb.addEventListener('change', () => { renderTruckCount(); renderFresh(); if (state.data.plan) renderUnassigned(state.data.plan); });
            const sw = document.createElement('span');
            sw.className = 'rt-dot';
            sw.style.background = t.ready ? truckColor(t.car_code) : 'transparent';
            sw.setAttribute('aria-hidden', 'true');
            const txt = document.createElement('span');
            txt.className = 'dp-truck-t';
            const nm = document.createElement('b');
            nm.textContent = t.name || t.car_code;
            const plate = document.createElement('span');
            plate.className = 'dp-truck-sub dp-plate';
            plate.textContent = t.name ? t.car_code : '';
            const sub = document.createElement('span');
            sub.className = 'dp-truck-sub';
            sub.textContent = t.ready ? 'տանում է մինչև ' + kgText(t.capacity_kg) + (t.center_ok ? ' · մտնում է կենտրոն' : '') : 'լրացրեք կարգավորումներում';
            txt.append(nm);
            if (t.name) txt.appendChild(plate);
            txt.appendChild(sub);
            lab.append(cb, sw, txt);
            box.appendChild(lab);
        });
        renderTruckCount();
    }
    const selectedTrucks = () => [...$('dpTrucks').querySelectorAll('input[type="checkbox"]:checked')].map(x => x.value);
    function renderTruckCount() {
        const ready = state.data.trucks.filter(t => t.ready).length;
        const picked = selectedTrucks();
        $('dpTrucksCount').textContent = ready ? 'Նշված է՝ ' + picked.length + ' / ' + ready : '';
        // свёрнутый шаг 1 — одной строкой: цвета и число работающих машин
        const sum = $('dpStep1Sum');
        sum.textContent = '';
        const dots = document.createElement('span');
        dots.className = 'dp-sumdots';
        dots.setAttribute('aria-hidden', 'true');
        picked.forEach(code => {
            const dot = document.createElement('span');
            dot.className = 'rt-dot';
            dot.style.background = truckColor(code);
            dots.appendChild(dot);
        });
        const txt = document.createElement('span');
        txt.textContent = 'Աշխատում է՝ ' + (ready > picked.length ? picked.length + ' / ' + ready + NB + 'մեքենա' : pl(picked.length, 'մեքենա'));
        sum.append(dots, txt);
    }

    // Шаги 1–2: пока рейсов нет — открыты; после сборки — свёрнуты в строку, «Բացել» раскрывает
    function renderSteps() {
        const d = state.data, plan = !!d.plan, o = d.orders;
        [['dpStep1', 'dpStep1Tog'], ['dpStep2', 'dpStep2Tog']].forEach(([id, tog]) => {
            const open = !plan || state.stepsOpen.has(id);
            $(id).classList.toggle('is-done', plan);
            $(id).classList.toggle('is-folded', !open);
            $(tog).hidden = !plan;
            $(tog).setAttribute('aria-expanded', String(open));
            $(tog).firstElementChild.textContent = open ? 'Ծալել' : 'Բացել';
        });
        const sum = $('dpStep2Sum');
        sum.textContent = '';
        const main = document.createElement('span');
        main.textContent = o.count ? pl(o.count, 'պատվեր') + ' · ' + pl(o.customers, 'խանութ') + ' · ' + kgText(o.kg) : 'Պատվերներ չկան';
        sum.appendChild(main);
        if (o.no_coords) {
            const w = document.createElement('span');
            w.className = 'is-warn';
            w.innerHTML = '<i class="fas fa-location-dot" aria-hidden="true"></i> ';
            w.appendChild(document.createTextNode(pl(o.no_coords, 'խանութ') + '՝ առանց կետի'));
            sum.appendChild(w);
        }
        $('dpTrucksCount').hidden = plan && !state.stepsOpen.has('dpStep1');
    }
    function toggleStep(id) {
        if (state.stepsOpen.has(id)) state.stepsOpen.delete(id); else state.stepsOpen.add(id);
        renderSteps();
        // карты внутри шага, нарисованные пока он был свёрнут (0×0), — перерисовать по настоящему размеру
        if (id === 'dpStep2' && state.stepsOpen.has(id)) { if ($('dpNoCoords').open) ensurePickMap(); if ($('dpGeoSug').open) renderGeoSug(); }
    }
    // Открыть свёрнутый шаг (кнопка внутри него или «тут нужно поправить»)
    function unfoldStep(id) {
        if (!state.data.plan || state.stepsOpen.has(id)) return;
        state.stepsOpen.add(id);
        renderSteps();
        if (id === 'dpStep2') { if ($('dpNoCoords').open) ensurePickMap(); if ($('dpGeoSug').open) renderGeoSug(); }
    }
    // Кнопка в подсказке «…в 1-ին քայլ…»: раскрыть шаг и перейти к нему
    function stepButton(id, text) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-stepbtn';
        b.innerHTML = '<i class="fas fa-arrow-up" aria-hidden="true"></i><span></span>';
        b.lastChild.textContent = text;
        b.addEventListener('click', () => {
            unfoldStep(id);
            $(id).scrollIntoView({ behavior: calm() ? 'auto' : 'smooth', block: 'start' });
            $(id + 'Title').setAttribute('tabindex', '-1');
            $(id + 'Title').focus({ preventScroll: true });
        });
        return b;
    }

    // Крупные цифры: [значение, подпись]
    function statTiles(box, items) {
        box.textContent = '';
        items.forEach(([v, k]) => {
            const div = document.createElement('div');
            div.className = 'dp-stat';
            const b = document.createElement('b');
            b.textContent = v;
            const s = document.createElement('span');
            s.textContent = k;
            div.append(b, s);
            box.appendChild(div);
        });
    }

    function renderOrders() {
        const o = state.data.orders;
        statTiles($('dpOrderStats'), [[fmt(o.count), 'պատվեր'], [fmt(o.customers), 'խանութ'], [kgText(o.kg), 'քաշը'], [fmt(Math.round(num(o.revenue) || 0)) + NB + '֏', 'գումարը']]);
        $('dpOrdersEmpty').hidden = !!o.count;
        const attn = $('dpAttn');
        attn.textContent = '';
        const add = (tone, ico, text, btnText, targetId) => {
            const li = document.createElement('li');
            li.className = 'dp-attn-item ' + tone;
            li.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span class="dp-attn-t"></span>';
            li.querySelector('.dp-attn-t').textContent = text;
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            b.textContent = btnText;
            b.addEventListener('click', () => {
                const el = $(targetId);
                el.open = true;
                el.scrollIntoView({ block: 'start', behavior: 'smooth' });
                el.querySelector('summary').focus();
            });
            li.appendChild(b);
            attn.appendChild(li);
        };
        if (o.no_coords) add('is-warn', 'fa-location-dot', pl(o.no_coords, 'խանութի') + ' տեղը քարտեզում նշված չէ (' + kgText(o.no_coords_kg)
            + ')։ Մինչև չնշեք, դրանք երթերի մեջ չեն մտնի։', 'Նշել քարտեզում', 'dpNoCoords');
        const bl = state.data.backlog || [];
        const added = bl.filter(x => x.added).length;
        if (bl.length) add('', 'fa-clock-rotate-left', 'Նախորդ օրերից մնացել է ' + pl(bl.length, 'չառաքված պատվեր')
            + (added ? ', որից ' + added + '-ը ավելացրել եք այսօրվա առաքմանը' : '') + '։ Ստուգեք՝ պե՞տք է դրանք տանել այսօր։', 'Դիտել', 'dpBacklog');
        if (o.excluded) add('', 'fa-ban', pl(o.excluded, 'պատվեր') + ' նշել եք «այսօր չենք տանում»։', 'Դիտել', 'dpExcluded');
        const gs = geoSug();
        if (gs.count) add('is-warn', 'fa-location-crosshairs', 'Վարորդներն առաջարկում են նոր տեղ՝ ' + pl(gs.count, 'խանութի') + ' համար'
            + (gs.day_count ? ', որից ' + gs.day_count + '-ը այս օրվա խանութներ են' : '') + '։ Ստուգեք և ընդունեք կամ մերժեք։', 'Դիտել', 'dpGeoSug');
        attn.hidden = !attn.children.length;
        const info = [];
        if (o.shipped_before) info.push(pl(o.shipped_before, 'պատվեր') + ' արդեն առաքվել է');
        if (o.self_delivery) info.push(pl(o.self_delivery, 'պատվեր') + ' (' + kgText(o.self_delivery_kg) + ') մենեջերն ինքն է տանում');
        $('dpOrdersInfo').textContent = info.length ? 'Առաքման մեջ չեն մտնում՝ ' + info.join(', ') + '։' : '';
    }

    // ---------- Магазины без точки ----------
    function renderNoCoords() {
        const list = state.data.stops_no_coords || [];
        const box = $('dpNoCoords');
        box.hidden = !list.length;
        $('dpNoCoordsNote').textContent = list.length ? pl(list.length, 'խանութ') : '';
        const ul = $('dpPickList');
        ul.textContent = '';
        if (!list.some(s => s.customer_id === state.pickCid)) resetPick();
        list.forEach(s => {
            const li = document.createElement('li');
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'dp-pickitem';
            b.setAttribute('aria-pressed', String(s.customer_id === state.pickCid));
            b.innerHTML = '<b></b><span class="dp-pick-addr"></span><span class="dp-pick-kg"></span>';
            b.querySelector('b').textContent = s.name || s.code;
            b.querySelector('.dp-pick-addr').textContent = (s.code ? s.code + ' · ' : '') + (s.address || 'ERP-ում հասցե չկա');
            b.querySelector('.dp-pick-kg').textContent = kgText(s.kg) + ' · ' + (s.agent_name || s.agent_code || '');
            b.addEventListener('click', () => choosePick(s));
            li.appendChild(b);
            ul.appendChild(li);
        });
    }
    function resetPick() {
        state.pickCid = null;
        $('dpPickLabel').textContent = 'Նախ ընտրեք խանութը ձախ կողմում';
        $('dpPickCoord').value = '';
        $('dpPickCoord').disabled = true;
        $('dpPickSave').disabled = true;
        $('dpPickErr').textContent = '';
        if (state.pickMarker) { state.pickMarker.remove(); state.pickMarker = null; }
    }
    function choosePick(s) {
        state.pickCid = s.customer_id;
        $('dpPickList').querySelectorAll('.dp-pickitem').forEach((b, i) => b.setAttribute('aria-pressed', String(state.data.stops_no_coords[i].customer_id === s.customer_id)));
        $('dpPickLabel').textContent = '«' + (s.name || s.code) + '» խանութի կետը՝ սեղմեք քարտեզի վրա կամ տեղադրեք կոորդինատները';
        $('dpPickCoord').disabled = false;
        $('dpPickCoord').value = '';
        $('dpPickSave').disabled = true;
        $('dpPickErr').textContent = '';
        ensurePickMap();
        $('dpPickCoord').focus();
    }
    function ensurePickMap() {
        if (state.pickMap || typeof window.L === 'undefined') {
            if (state.pickMap) state.pickMap.invalidateSize();
            return;
        }
        const map = L.map($('dpPickMap'), { zoomSnap: 0.5, scrollWheelZoom: false });
        RoutesBasemap.add(map);
        map.setView(state.data.depot ? [state.data.depot.lat, state.data.depot.lon] : YEREVAN, 11);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        map.on('click', (e) => {
            if (state.pickCid === null) return;
            setPick(e.latlng.lat, e.latlng.lng);
        });
        state.pickMap = map;
        watchSize($('dpPickMap'), map, () => null);
    }
    function setPick(lat, lon) {
        $('dpPickCoord').value = lat.toFixed(6) + ', ' + lon.toFixed(6);
        $('dpPickSave').disabled = false;
        $('dpPickErr').textContent = '';
        if (state.pickMap) {
            if (!state.pickMarker) state.pickMarker = L.marker([lat, lon], { draggable: true, keyboard: false }).addTo(state.pickMap)
                .on('dragend', (ev) => { const p = ev.target.getLatLng(); setPick(p.lat, p.lng); });
            else state.pickMarker.setLatLng([lat, lon]);
        }
    }
    function parseCoord(s) {
        const m = /^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/.exec(String(s || ''));
        if (!m) return null;
        const lat = parseFloat(m[1].replace(',', '.')), lon = parseFloat(m[2].replace(',', '.'));
        if (!(lat >= 38.8 && lat <= 41.4 && lon >= 43.4 && lon <= 46.7)) return null;
        return [lat, lon];
    }
    async function savePick() {
        const p = parseCoord($('dpPickCoord').value);
        if (!p) { $('dpPickErr').textContent = 'Անհրաժեշտ են լայնություն և երկայնություն Հայաստանում, օրինակ՝ 40.17920, 44.49910'; return; }
        if (state.pickCid === null || state.busy) return;
        state.busy = true;
        $('dpPickSave').disabled = true;
        try {
            await api('POST', '/api/routes/geo-override', { customer_id: state.pickCid, lat: p[0], lon: p[1] });
            toast('Կետը պահպանված է։ Խանութը կարելի է տանել — այն կհայտնվի «Դեռ երթերում չեն» ցուցակում կամ երթերը կազմելիս։');
            resetPick();
            await reloadQuiet();
        } catch (e) {
            $('dpPickErr').textContent = e.message;
            $('dpPickSave').disabled = false;
        } finally { state.busy = false; }
    }
    async function reloadQuiet() {
        const seq = state.loadSeq;
        const data = await api('GET', '/api/routes/dispatch?date=' + encodeURIComponent(state.day));
        if (seq === state.loadSeq) setData(data);
    }

    // ---------- Точка магазина: «Փոխել տեղը» и предложения водителей (driver-geo-plan.md §5) ----------
    // Источник точки — плашкой у магазина (сервер: manual → driver → erp → gps)
    const COORD_HY = { erp: ['b-erp', 'ERP հասցե'], gps: ['b-gps', 'մենեջերի GPS'], driver: ['b-ok', 'վարորդի GPS'], manual: ['b-manual', 'ձեռքով'] };
    const GEO_REBUILD = 'Սեղմեք «Վերակազմել երթերը»՝ երթերը թարմացնելու համար։';
    // Точку сменили: рейсы сами не пересобираются — подсказка (если магазин в развозе этого дня) и перечитать день
    async function geoSaved(text, inDay) {
        const rebuild = !!state.data.plan && inDay;
        if (rebuild) state.geoChanged = state.day;
        toast(text + (rebuild ? ' ' + GEO_REBUILD : ''));
        await reloadQuiet();
    }
    function mapReady(el) {
        if (typeof window.L !== 'undefined') return true;
        el.classList.add('rt-map-fallback');
        el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Կոորդինատները կարելի է տեղադրել ձեռքով։';
        return false;
    }
    function smallMap(el) {
        const map = L.map(el, { zoomSnap: 0.5, scrollWheelZoom: false });
        RoutesBasemap.add(map);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        return map;
    }

    // «Փոխել տեղը»: диалог с картой — перетащить точку, кликнуть или вставить координаты; «авто» — убрать ручную
    function openGeo(stop) {
        if (state.busy) return;
        state.geoStop = stop;
        const src = COORD_HY[stop.coord_source];
        const has = num(stop.lat) !== null && num(stop.lon) !== null;
        $('dpGeoTitle').textContent = '«' + (stop.name || stop.code) + '» — խանութի տեղը';
        $('dpGeoLead').textContent = (stop.address ? stop.address + '։ ' : '') + (src ? 'Հիմա՝ ' + src[1] + '։ ' : '')
            + 'Քաշեք կետը կամ սեղմեք քարտեզի վրա այնտեղ, որտեղ մեքենան բեռնաթափում է, կամ տեղադրեք կոորդինատները։';
        $('dpGeoAuto').hidden = stop.coord_source !== 'manual';
        $('dpGeoAuto').disabled = false;
        $('dpGeoErr').textContent = '';
        $('dpGeoCoord').value = has ? stop.lat.toFixed(6) + ', ' + stop.lon.toFixed(6) : '';
        $('dpGeoSave').disabled = true;     // пока точку не сдвинули — сохранять нечего
        $('dpGeoDlg').showModal();
        const el = $('dpGeoMap');
        if (!state.geoMap && mapReady(el)) {
            state.geoMap = smallMap(el);
            state.geoMap.on('click', (e) => setGeo(e.latlng.lat, e.latlng.lng, true));
        }
        if (state.geoMap) {
            state.geoMap.invalidateSize();
            if (state.geoMarker) { state.geoMarker.remove(); state.geoMarker = null; }
            if (has) { setGeo(stop.lat, stop.lon, false); state.geoMap.setView([stop.lat, stop.lon], 16); }
            else state.geoMap.setView(state.data.depot ? [state.data.depot.lat, state.data.depot.lon] : YEREVAN, 11);
        }
        $('dpGeoCoord').focus();
    }
    // Точка диалога; fromMap — с карты (клик, перетаскивание): вписать в поле и разрешить «Պահպանել»
    function setGeo(lat, lon, fromMap) {
        if (fromMap) {
            $('dpGeoCoord').value = lat.toFixed(6) + ', ' + lon.toFixed(6);
            $('dpGeoSave').disabled = false;
            $('dpGeoErr').textContent = '';
        }
        if (!state.geoMap) return;
        if (!state.geoMarker) state.geoMarker = L.marker([lat, lon], { draggable: true, keyboard: false }).addTo(state.geoMap)
            .on('dragend', (ev) => { const p = ev.target.getLatLng(); setGeo(p.lat, p.lng, true); });
        else state.geoMarker.setLatLng([lat, lon]);
        if (!fromMap) state.geoMap.panTo([lat, lon], { animate: false });
    }
    async function saveGeo(auto) {
        const stop = state.geoStop;
        if (!stop || state.busy) return;
        const p = auto ? null : parseCoord($('dpGeoCoord').value);
        if (!auto && !p) { $('dpGeoErr').textContent = 'Անհրաժեշտ են լայնություն և երկայնություն Հայաստանում, օրինակ՝ 40.17920, 44.49910'; return; }
        state.busy = true;
        $('dpGeoSave').disabled = true;
        $('dpGeoAuto').disabled = true;
        try {
            await api('POST', '/api/routes/geo-override', { customer_id: stop.customer_id, lat: p ? p[0] : null, lon: p ? p[1] : null });
            $('dpGeoDlg').close();
            state.busy = false;
            await geoSaved('«' + (stop.name || stop.code) + '»՝ ' + (auto ? 'կետը նորից ավտոմատ է։' : 'նոր կետը պահպանված է։'), true);
        } catch (e) {
            $('dpGeoErr').textContent = e.message;
            $('dpGeoSave').disabled = false;
        } finally {
            state.busy = false;
            $('dpGeoAuto').disabled = false;
        }
    }

    // Предложения водителей «Կետը սխալ է»: слева список, справа старая (серая) и новая (зелёная) точка
    const geoSug = () => (isObj(state.data.geo_suggestions) ? state.data.geo_suggestions : { count: 0, day_count: 0, items: [] });
    // Одна карточка на магазин: последнее предложение; было несколько — сколько (решение одно на магазин)
    function sugText(x) {
        const far = num(x.distance_m) !== null ? fmt(x.distance_m) + NB + 'մ հին կետից' : 'հին կետ չկար';
        return 'Վարորդ՝ ' + (x.driver_name || '—') + ' · ' + dateRu(x.date) + ' · ' + far
            + (num(x.accuracy) !== null ? ' · ճշտությունը ±' + fmt(Math.round(x.accuracy)) + NB + 'մ' : '')
            + (x.suggestions > 1 ? ' · ընդամենը ' + pl(x.suggestions, 'առաջարկ') + ', ցույց է տրված վերջինը' : '');
    }
    function renderGeoSug() {
        const gs = geoSug(), items = Array.isArray(gs.items) ? gs.items : [];
        const box = $('dpGeoSug');
        box.hidden = !items.length;
        $('dpGeoSugNote').textContent = gs.count ? pl(gs.count, 'խանութ') : '';
        $('dpGeoSugMore').textContent = gs.count > items.length ? 'Ցույց են տրված առաջին ' + items.length + '-ը՝ նախ այս օրվա խանութները։' : '';
        if (!items.some(x => x.event_id === state.sugSel)) state.sugSel = items.length ? items[0].event_id : null;
        const ul = $('dpGeoSugList');
        ul.textContent = '';
        items.forEach(x => {
            const li = document.createElement('li');
            li.className = 'dp-sug';
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'dp-pickitem';
            b.setAttribute('aria-pressed', String(x.event_id === state.sugSel));
            b.innerHTML = '<b></b><span class="dp-pick-addr"></span><span class="dp-pick-kg"></span>';
            b.querySelector('b').textContent = (x.name || x.code) + (x.in_day ? '' : ' (այս օրը չկա)');
            b.querySelector('.dp-pick-addr').textContent = sugText(x);
            b.querySelector('.dp-pick-kg').textContent = x.note ? '«' + x.note + '»' : '';
            b.addEventListener('click', () => { state.sugSel = x.event_id; renderGeoSug(); });
            const acts = document.createElement('div');
            acts.className = 'dp-sug-acts';
            [['Ընդունել', 'accepted', 'rt-btn-primary', 'fa-check'], ['Մերժել', 'rejected', 'rt-btn-ghost', 'fa-xmark']].forEach(([t, dec, cls, ico]) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'rt-btn rt-btn-sm ' + cls;
                btn.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span></span>';
                btn.lastChild.textContent = t;
                btn.setAttribute('aria-label', t + '՝ ' + (x.name || x.code));
                btn.addEventListener('click', () => decideSug(x, dec));
                acts.appendChild(btn);
            });
            li.append(b, acts);
            ul.appendChild(li);
        });
        if (box.open) drawSug(items.find(x => x.event_id === state.sugSel));
    }
    function drawSug(x) {
        const el = $('dpGeoSugMap');
        if (!state.sugMap) {
            if (!mapReady(el)) return;
            state.sugMap = smallMap(el);
            state.sugLayer = L.layerGroup().addTo(state.sugMap);
            watchSize(el, state.sugMap, () => null);
        }
        state.sugLayer.clearLayers();
        state.sugMap.invalidateSize();
        if (!x) return;
        const now = [x.lat, x.lon], pts = [now];
        if (x.current) {
            const old = [x.current.lat, x.current.lon], src = COORD_HY[x.current.source];
            pts.push(old);
            L.polyline([old, now], { color: '#8791a3', weight: 2, dashArray: '4 6' }).addTo(state.sugLayer);
            L.circleMarker(old, { radius: 8, color: '#0c0f14', weight: 2, fillColor: '#8791a3', fillOpacity: 1 })
                .bindTooltip('Հին կետը' + (src ? '՝ ' + src[1] : '')).addTo(state.sugLayer);
        }
        L.circleMarker(now, { radius: 9, color: '#0c0f14', weight: 2, fillColor: '#45d98f', fillOpacity: 1 })
            .bindTooltip('Նոր կետը՝ ' + esc(x.driver_name || 'վարորդ')).addTo(state.sugLayer);
        if (pts.length > 1) state.sugMap.fitBounds(pts, { padding: [30, 30], maxZoom: 17, animate: false });
        else state.sugMap.setView(now, 16);
    }
    async function decideSug(x, decision) {
        if (state.busy) return;
        state.busy = true;
        hideActionError();
        try {
            const r = await api('POST', '/api/routes/geo-suggest/decide', { event_id: x.event_id, decision });
            state.busy = false;
            const name = '«' + (x.name || x.code) + '»՝ ';
            const more = Array.isArray(r.superseded) && r.superseded.length ? ' Նույն խանութի մյուս ' + pl(r.superseded.length, 'առաջարկ') + ' փակվեց։' : '';
            if (decision === 'accepted') await geoSaved(name + 'վարորդի կետն ընդունված է։' + more, !!x.in_day);
            else {
                toast(name + 'առաջարկը մերժված է։' + (r.manual_same
                    ? ' Ուշադրություն՝ խանութի ձեռքով նշված կետը հենց այս կետն է և չի փոխվել։ Փոխելու համար օգտվեք «Փոխել տեղը»-ից։' : ''));
                await reloadQuiet();
            }
        } catch (e) {
            state.busy = false;
            showActionError(e);
            // уже решено в другой вкладке — убрать устаревшую карточку
            if (e.status === 404) { try { await reloadQuiet(); } catch (e2) { /* ошибка уже показана */ } }
        } finally { state.busy = false; }
    }

    // ---------- Время у магазина «Ժամանակ խանութում» (ответ владельца №50) ----------
    // Своё время магазина — постоянная часть разгрузки (парковка, приёмка, документы) вместо общей нормы на точку; время на
    // груз программа добавляет сама. Данные диалога — GET /api/routes/customer-vehicles?customer_id=… (свежие: значение,
    // нормы, время по факту); сохраняется только время — POST /api/routes/customer-vehicles {customer_id, unload_min}:
    // допуск и окно приёма не пересылаются. Рейсы сами не пересобираются — подсказка в уведомлении, как после смены точки.
    const UNLOAD_REBUILD = 'Վերակազմեք երթերը, որ հաշվի առնվի։';
    const UNLOAD_BAD = 'Գրեք ամբողջ թիվ՝ 1-ից մինչև 120 րոպե, կամ թողեք դաշտը դատարկ։';
    const minutesText = (v) => fmt(v, 1) + NB + 'րոպե';
    // своё время магазина из ответа дня (store_unload: клиент → мин); не задано — null (общая норма)
    const ownUnload = (stop) => (isObj(state.data.store_unload) ? num(state.data.store_unload[stop.customer_id]) : null);
    // Подсказка — как посчитает «Развоз» (та же логика, что в «Условиях магазина» /routes/settings): пустое поле — обычное
    // время или своё время магазина по факту (unload_auto_min); есть разгрузки по GPS (unload_visits) — введённое смешается с фактом.
    // «По факту» — только если отличается от нормы на 0,05 мин и больше: сервер округляет unload_auto_min до 0,1, а норму
    // строки обучения — до 0,01 (8,4 и 8,37 — одно и то же «обычное» время)
    function unloadHint(x, norms) {
        const auto = num(x.unload_auto_min), perStop = num(norms.per_stop_min);
        const empty = auto !== null && perStop !== null && Math.abs(auto - perStop) >= 0.05
            ? minutesText(auto) + ' (ըստ փաստի)' : 'սովորական ' + minutesText(perStop);
        const fact = num(x.unload_visits) ? ' Ըստ վարորդների GPS-ի՝ այս խանութում արդեն եղել է ' + pl(x.unload_visits, 'բեռնաթափում')
            + '։ Ձեր գրած ժամանակը ծրագիրը կհամադրի փաստի հետ՝ որքան շատ բեռնաթափում, այնքան ավելի մոտ փաստին։' : '';
        return 'Քանի րոպե է մեքենան կանգնում այս խանութի մոտ՝ կայանում, ընդունում, փաստաթղթեր։ Բեռի ժամանակը ('
            + minutesText(norms.per_tonne_min) + ' տոննայի համար) ծրագիրը կավելացնի ինքը։' + fact + ' Դատարկ՝ ' + empty + '։';
    }
    const lockUnload = (on) => ['dpUnloadMin', 'dpUnloadSave', 'dpUnloadClear'].forEach(id => { $(id).disabled = on; });
    function markUnload(bad) {
        $('dpUnloadMin').classList.toggle('is-invalid', bad);
        if (bad) $('dpUnloadMin').setAttribute('aria-invalid', 'true'); else $('dpUnloadMin').removeAttribute('aria-invalid');
    }
    async function openUnload(stop) {
        if (state.busy) return;
        const seq = ++state.unloadSeq;
        state.unloadStop = stop;
        state.unloadInfo = null;
        $('dpUnloadLead').textContent = '«' + (stop.name || stop.code) + '»' + (stop.address ? '՝ ' + stop.address : '');
        $('dpUnloadMin').value = ownUnload(stop) !== null ? String(ownUnload(stop)) : '';
        markUnload(false);
        $('dpUnloadHint').textContent = 'Բեռնում եմ խանութի տվյալները…';
        $('dpUnloadErr').textContent = '';
        $('dpUnloadClear').hidden = true;
        lockUnload(true);           // пока не пришли свежие данные магазина — сохранять нечего
        $('dpUnloadDlg').showModal();
        try {
            const r = await api('GET', '/api/routes/customer-vehicles?customer_id=' + encodeURIComponent(stop.customer_id));
            if (seq !== state.unloadSeq || !$('dpUnloadDlg').open) return;
            const x = Array.isArray(r.customers) ? r.customers[0] : null;
            // магазина нет в данных ERP раздела (новый) — сервер не сохранит и время
            if (!isObj(x) || !isObj(r.unload_norms)) throw new Error('Խանութը չի գտնվել — թարմացրեք էջը');
            state.unloadInfo = x;
            const own = num(x.unload_min);
            $('dpUnloadMin').value = own !== null ? String(own) : '';
            $('dpUnloadHint').textContent = unloadHint(x, r.unload_norms);
            $('dpUnloadClear').hidden = own === null;
            lockUnload(false);
            $('dpUnloadMin').focus();
        } catch (e) {
            if (seq !== state.unloadSeq) return;
            $('dpUnloadHint').textContent = '';
            $('dpUnloadErr').textContent = e.message;
        }
    }
    function readUnload() {
        const input = $('dpUnloadMin');
        // нечисло в поле type=number браузер отдаёт как '' — это не «пусто»: иначе сохранённое время стёрлось бы молча
        if (input.validity && input.validity.badInput) throw new Error(UNLOAD_BAD);
        const raw = input.value.trim();
        if (raw === '') return null;
        const value = Number(raw);
        if (!Number.isInteger(value) || value < 1 || value > 120) throw new Error(UNLOAD_BAD);
        return value;
    }
    // clear — «Հեռացնել»: снова обычное время; пустое поле при «Պահպանել» — то же. Значение не изменилось (например,
    // пустое поле у магазина без своего времени) — сохранять нечего: диалог закрывается без запроса и уведомления
    async function saveUnload(clear) {
        const stop = state.unloadStop, x = state.unloadInfo;
        if (!stop || !x || state.busy) return;
        let value = null;
        if (!clear) {
            try { value = readUnload(); } catch (e) {
                $('dpUnloadErr').textContent = e.message;
                markUnload(true);
                $('dpUnloadMin').focus();
                return;
            }
        }
        if (value === num(x.unload_min)) { $('dpUnloadDlg').close(); return; }
        state.busy = true;
        lockUnload(true);
        $('dpUnloadErr').textContent = '';
        try {
            await api('POST', '/api/routes/customer-vehicles', { customer_id: stop.customer_id, unload_min: value });
        } catch (e) {
            $('dpUnloadErr').textContent = e.message;
            return;
        } finally {
            state.busy = false;
            lockUnload(false);
        }
        $('dpUnloadDlg').close();
        // время изменилось, а рейсы уже есть — подсказать пересборку, как после смены точки
        toast('«' + (stop.name || stop.code) + '»՝ ' + (value === null ? 'ժամանակը խանութում նորից սովորական է։'
            : 'ժամանակը խանութում պահպանված է (' + minutesText(value) + ')։') + (state.data.plan ? ' ' + UNLOAD_REBUILD : ''));
        try { await reloadQuiet(); } catch (e) { showActionError(e); }
    }

    // ---------- Заказы прошлых дней и исключённые ----------
    function orderLine(o, btnText, onClick) {
        const li = document.createElement('li');
        li.className = 'dp-oitem';
        const t = document.createElement('span');
        t.className = 'dp-oitem-t';
        const b = document.createElement('b');
        b.textContent = o.name || o.code || ('հաճախորդ ' + o.customer_id);
        const s = document.createElement('span');
        s.textContent = (o.code ? o.code + ' · ' : '') + 'պատվեր ' + (o.doc_num || '') + (o.order_date ? ', ' + dateRu(o.order_date) : '')
            + ' · ' + kgText(o.kg) + ' · ' + money(o.revenue)
            + (o.deferred ? ' · կտարվի ' + dayHuman(state.data.defer_to, true) : '')
            + (o.carried ? ' · տեղափոխված է նախորդ օրից' : '');
        t.append(b, s);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        btn.textContent = btnText;
        btn.addEventListener('click', onClick);
        li.append(t, btn);
        return li;
    }
    function needPlan() {
        if (state.data.plan) return true;
        showActionError(new Error('Նախ սեղմեք «Կազմել երթերը» — փոփոխությունները պահպանվում են օրվա պլանում։'));
        return false;
    }
    function renderOrderLists() {
        const bl = state.data.backlog || [];
        $('dpBacklog').hidden = !bl.length;
        $('dpBacklogNote').textContent = bl.length ? pl(bl.length, 'պատվեր') : '';
        const ul = $('dpBacklogList');
        ul.textContent = '';
        bl.forEach(o => ul.appendChild(orderLine(o, o.added ? 'Հանել առաքումից' : 'Ավելացնել առաքմանը',
            () => { if (needPlan()) edit({ action: o.added ? 'exclude' : 'include', order: o.isn }, o.added ? 'Պատվերը հանվեց առաքումից' : 'Պատվերն ավելացվեց — սեղմեք «Վերակազմել երթերը» կամ տեղափոխեք կետը որևէ երթ'); })));
        const ex = state.data.excluded || [];
        $('dpExcluded').hidden = !ex.length;
        $('dpExcludedNote').textContent = ex.length ? pl(ex.length, 'պատվեր') : '';
        const ul2 = $('dpExcludedList');
        ul2.textContent = '';
        ex.forEach(o => ul2.appendChild(orderLine(o, 'Վերադարձնել', () => edit({ action: 'include', order: o.isn }, 'Պատվերը վերադարձվեց — կետը «Դեռ երթերում չեն» ցուցակում է կամ իր երթում'))));
    }

    // ---------- Рейсы ----------
    function deltaText(delta) {
        const v = num(delta);
        if (v === null || Math.abs(v) < 0.05) return 'ճանապարհի երկարությունը չի փոխվել';
        return v > 0 ? 'ճանապարհը երկարեց ' + fmt(v, 1) + NB + 'կմ-ով' : 'ճանապարհը կարճացավ ' + fmt(-v, 1) + NB + 'կմ-ով';
    }

    function renderPlan(plan) {
        const sm = plan.summary, base = plan.baseline;
        statTiles($('dpPlanStats'), [[fmt(sm.trucks), 'մեքենա'], [fmt(sm.trips), 'երթ'], [fmt(sm.stops), 'խանութ'],
            ['≈ ' + fmt(sm.km) + NB + 'կմ', 'ճանապարհ'], ['≈ ' + fmt(sm.liters) + NB + 'լ', 'դիզել'],
            ['≈ ' + fmt(sm.operating_cost_amd) + NB + '֏', 'դիզել և մաշվածք']]);
        // Под цифрами — сравнение с обычной развозкой по менеджерам и чего не хватает в расчёте
        const lead = $('dpMainLead');
        lead.textContent = '';
        const saveRow = (tone, ico, parts) => {
            const row = document.createElement('div');
            row.className = 'dp-save' + (tone ? ' ' + tone : '');
            row.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span class="dp-save-t"></span>';
            const tx = row.lastChild;
            parts.forEach(([text, strong]) => {
                if (strong) { const b = document.createElement('b'); b.textContent = text; tx.appendChild(b); } else tx.appendChild(document.createTextNode(text));
            });
            lead.appendChild(row);
        };
        if (base && num(base.km) !== null) {
            const diff = Math.round(base.km - sm.km);
            const dl = base.liters !== null ? Math.round(base.liters - sm.liters) : 0;
            if (diff > 0) saveRow('', 'fa-arrow-trend-down', [['Սա '], [fmt(diff) + NB + 'կմ-ով կարճ է', true],
                [', քան եթե յուրաքանչյուր մենեջերի պատվերները տաներ իր սովորական մեքենան'],
                ...(dl > 0 ? [[' (≈ '], [fmt(dl) + NB + 'լ', true], [' դիզելի խնայողություն)']] : []), ['։']]);
            else saveRow('is-neutral', 'fa-scale-balanced', [[diff < 0
                ? 'Սա ' + fmt(-diff) + NB + 'կմ-ով երկար է, քան սովորական բաշխումը ըստ մենեջերների — ստուգեք ամրացված երթերը։'
                : 'Ճանապարհը նույնն է, ինչ սովորական բաշխումը ըստ մենեջերների։']]);
        }
        if (plan.coverage && !plan.coverage.complete) {
            const c = plan.coverage;
            saveRow('is-warn', 'fa-triangle-exclamation', [['Երթերում է '], [fmt(c.stops_assigned) + ' / ' + fmt(c.stops_total) + ' խանութ', true],
                ['։ Խնայողությունը հաշվված է միայն երթերում եղած պատվերների համար։']]);
        }
        const costNotes = [];
        costNotes.push(sm.traffic && sm.traffic.status === 'provider_forecast'
            ? 'Յանդեքսի երթևեկության կանխատեսումը՝ հերթափոխի սկզբի համար, ուշ հատվածների ժամանակը նույն մատրիցով է'
            : sm.traffic && sm.traffic.status === 'validated'
            ? 'ժամային արագությունները՝ GPS պատմությունից, ընթացիկ խցանումները հայտնի չեն'
            : 'արագությունը միջին է, ընթացիկ խցանումները հայտնի չեն');
        if (sm.traffic && sm.traffic.reason) costNotes.push('Յանդեքսի տվյալները հասանելի չեն․ օգտագործվում է միջին արագությունը');
        if (sm.loading_minutes) costNotes.push('պահեստում բեռնումը՝ ' + fmt(sm.loading_minutes) + ' րոպե');
        if (sm.loading_configured === false) costNotes.push('պահեստում բեռնման ժամանակը դեռ ամբողջությամբ նշված չէ');
        if (sm.fuel_load_unconfigured) costNotes.push(fmt(sm.fuel_load_unconfigured) + ' երթի համար բեռից կախված վառելիքի նորմերը նշված չեն');
        if (sm.wear_unconfigured) costNotes.push(fmt(sm.wear_unconfigured) + ' երթի մաշվածքի արժեքը նշված չէ');
        if (sm.fuel_price_estimated) costNotes.push('դիզելի գինը պայմանական է՝ ' + money(sm.fuel_price_amd) + '/լ');
        if (costNotes.length) {
            const note = document.createElement('p');
            note.className = 'dp-costnote';
            const text = costNotes.join('։ ') + '։ Նորմերը լրացրեք մեքենաների կարգավորումներում։';
            note.textContent = text.charAt(0).toUpperCase() + text.slice(1);
            lead.appendChild(note);
        }
        renderBoard(plan);
        renderOverflow(plan);
        renderUnassigned(plan);
        renderTruckCards(plan);
        renderBaseline(plan);
        if (!$('dpMapBox').open) return;
        drawMap();
    }

    // ---------- Шкала дня: строка на машину, рейсы — полосы по часам ----------
    const toMin = (s) => clockMin(s);     // «HH:MM» и «HH:MM (+1)» после полуночи
    const calm = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const tripBad = (tr) => !!(tr.over_time || tr.over_capacity || tr.no_truck || tr.window_miss || tr.center_miss || tr.vehicle_miss);
    function renderBoard(plan) {
        const box = $('dpBoard'), d = state.data;
        box.textContent = '';
        const key = d.day + '|' + (d.built_at || '');
        box.classList.toggle('is-static', state.boardKey === key);
        state.boardKey = key;
        box.hidden = !plan.trucks.length;
        if (!plan.trucks.length) return;
        const ws = toMin(d.work_start) ?? 540, we = toMin(d.work_end) ?? 1080;
        let lo = ws, hi = we, loading = false;
        plan.trucks.forEach(t => t.trips.forEach(tr => {
            [tr.loading_start, tr.depart].forEach(x => { const m = toMin(x); if (m !== null) lo = Math.min(lo, m); });
            const r = toMin(tr.return);
            if (r !== null) hi = Math.max(hi, r);
            if (tr.loading_minutes) loading = true;
        }));
        const t0 = Math.floor((lo - 30) / 60) * 60, t1 = Math.ceil((hi + 30) / 60) * 60, span = t1 - t0;
        const x = (m) => ((m - t0) / span * 100).toFixed(3) + '%';
        const w = (a, b) => (Math.max(0, b - a) / span * 100).toFixed(3) + '%';

        const head = document.createElement('div');
        head.className = 'dp-board-head';
        head.innerHTML = '<h3 class="dp-board-t" id="dpBoardTitle"><i class="fas fa-clock" aria-hidden="true"></i>Մեքենաների օրը ըստ ժամերի</h3>'
            + '<div class="dp-board-legend" aria-hidden="true"><span><i class="dp-lg-work"></i></span><span><i class="dp-lg-over"></i></span></div>';
        const lg = head.querySelectorAll('.dp-board-legend span');
        lg[0].appendChild(document.createTextNode('աշխատանքային ժամ՝ ' + hhmm(ws) + '–' + hhmm(we)));
        lg[1].appendChild(document.createTextNode(hhmm(we) + '-ից հետո'));
        if (loading) head.lastChild.insertAdjacentHTML('beforeend', '<span><i class="dp-lg-load"></i>բեռնում պահեստում</span>');
        box.appendChild(head);

        const grid = document.createElement('div');
        grid.className = 'dp-board-grid';
        grid.style.setProperty('--dp-hours', String(span / 60));
        const axis = document.createElement('div');
        axis.className = 'dp-axis';
        axis.setAttribute('aria-hidden', 'true');
        const step = span > 13 * 60 ? 120 : 60;
        for (let m = t0; m <= t1; m += step) {
            const s = document.createElement('span');
            s.textContent = hhmm(m).replace(/^0/, '');
            s.style.left = x(m);
            // на телефоне подписи — раз в три часа (остальные is-minor скрыты стилями)
            s.className = (m === ws || m === we ? 'is-edge' : '') + (((m - t0) / step) % 3 ? ' is-minor' : '');
            axis.appendChild(s);
        }
        grid.appendChild(axis);
        // «сейчас» — только в сегодняшнем дне
        let now = null;
        if (d.day === d.today) { const n = new Date(); now = n.getHours() * 60 + n.getMinutes(); if (now < t0 || now > t1) now = null; }

        plan.trucks.forEach((t, ti) => {
            const color = truckColor(t.car_code);
            const lab = document.createElement('button');
            lab.type = 'button';
            lab.className = 'dp-blabel';
            lab.dataset.truck = t.car_code;
            lab.setAttribute('aria-label', 'Ցույց տալ քարտեզում՝ ' + truckLabel(t) + ', ' + pl(t.trips.length, 'երթ') + ', վերադարձ ' + t.return);
            lab.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span class="dp-blabel-t"><b></b><small></small></span>';
            lab.querySelector('.rt-dot').style.background = color;
            lab.querySelector('b').textContent = t.name || t.car_code;
            lab.querySelector('small').textContent = t.name ? t.car_code : '';
            lab.addEventListener('click', () => focusFromBoard(t, null));

            const track = document.createElement('div');
            track.className = 'dp-track';
            const zone = (cls, a, b) => { const z = document.createElement('span'); z.className = cls; z.style.left = x(a); z.style.width = w(a, b); track.appendChild(z); };
            zone('dp-work', ws, we);
            zone('dp-over', we, t1);
            t.trips.forEach((tr, i) => {
                const ls = toMin(tr.loading_start), dep = toMin(tr.depart), ret = toMin(tr.return);
                if (dep === null || ret === null) return;
                if (tr.loading_minutes && ls !== null && ls < dep) zone('dp-load', ls, dep);
                const bar = document.createElement('button');
                bar.type = 'button';
                bar.className = 'dp-bar' + (tripBad(tr) ? ' is-bad' : '') + (tr.late ? ' is-late' : '');
                bar.dataset.truck = t.car_code;
                bar.dataset.trip = String(tr.id);
                bar.style.left = x(dep);
                bar.style.width = w(dep, ret);
                bar.style.setProperty('--dp-c', color);
                bar.style.setProperty('--dp-delay', (ti * 70 + i * 40) + 'ms');
                const info = (t.trips.length > 1 ? 'Երթ ' + (i + 1) + ' · ' : '') + pl(tr.stops.length, 'կետ')
                    + (tr.load_pct !== null && tr.load_pct !== undefined ? ' · ' + tr.load_pct + '%' : '');
                const full = truckLabel(t) + ' · երթ ' + (i + 1) + ' · ' + tr.depart + ' → ' + tr.return + ' · ' + pl(tr.stops.length, 'կետ')
                    + ' · ' + kgText(tr.kg) + ' · ≈ ' + fmt(tr.km) + NB + 'կմ';
                bar.title = full;
                bar.setAttribute('aria-label', info + ' — ' + full + ' — ցույց տալ քարտեզում');
                const bt = document.createElement('span');
                bt.className = 'dp-bar-t';
                bt.textContent = info;
                bar.appendChild(bt);
                // засечки — приезд в каждый магазин
                tr.stops.forEach(s => {
                    const e = toMin(s.eta);
                    if (e === null || ret <= dep) return;
                    const tick = document.createElement('i');
                    tick.className = 'dp-tick';
                    tick.style.left = ((e - dep) / (ret - dep) * 100).toFixed(2) + '%';
                    bar.appendChild(tick);
                });
                bar.addEventListener('click', () => focusFromBoard(t, tr));
                track.appendChild(bar);
            });
            if (now !== null) { const n = document.createElement('span'); n.className = 'dp-now'; n.style.left = x(now); n.title = 'Հիմա'; track.appendChild(n); }

            const end = document.createElement('div');
            end.className = 'dp-bend';
            end.innerHTML = '<b></b><small></small>';
            end.firstChild.textContent = t.return;
            if (t.over_time) end.firstChild.className = 'is-bad'; else if (t.late) end.firstChild.className = 'is-late';
            end.lastChild.textContent = 'վերադարձ · ≈ ' + fmt(t.km) + NB + 'կմ';
            grid.append(lab, track, end);
        });
        // на узком экране шкала не сжимается, а прокручивается вбок
        const scroll = document.createElement('div');
        scroll.className = 'dp-board-scroll';
        scroll.appendChild(grid);
        box.appendChild(scroll);
        syncFocus();
    }

    // Выбранное на карте — подсвечено и на шкале, и в карточках машин
    function syncFocus() {
        const f = state.mapFocus;
        $('dpBoard').querySelectorAll('.dp-blabel').forEach(b => b.setAttribute('aria-pressed', String(!!f && f.truck === b.dataset.truck && f.trip == null)));
        $('dpBoard').querySelectorAll('.dp-bar').forEach(b => b.setAttribute('aria-pressed', String(!!f && f.trip != null && String(f.trip) === b.dataset.trip)));
        $('dpTruckCards').querySelectorAll('.dp-tcard').forEach(c => c.classList.toggle('is-focus', !!f && f.truck === c.dataset.truck));
    }

    // Нажали машину или рейс на шкале: он же на карте, карточка машины раскрыта и видна рядом с картой
    function focusFromBoard(t, tr) {
        state.open.add(t.car_code);
        renderTruckCards(state.data.plan);
        if (!$('dpMapBox').open) { state.mapFocus = { truck: t.car_code, trip: tr ? tr.id : null }; $('dpMapBox').open = true; syncFocus(); }
        else setMapFocus({ truck: t.car_code, trip: tr ? tr.id : null });
        // к карточке (и рейсу): рядом закреплена карта, а на узком экране карта ниже рейсов — к ней ведёт «Քարտեզում»
        const card = [...$('dpTruckCards').querySelectorAll('.dp-tcard')].find(c => c.dataset.truck === t.car_code);
        const target = tr && card ? card.querySelector('.dp-trip[data-trip="' + tr.id + '"]') || card : card;
        if (target) target.scrollIntoView({ behavior: calm() ? 'auto' : 'smooth', block: 'start' });
    }

    // Не помещается: что именно и что делать — простыми словами
    function renderOverflow(plan) {
        const box = $('dpOverflow');
        box.textContent = '';
        const bad = [];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => {
            const who = truckLabel(t) + ', երթ ' + (i + 1) + '՝ ';
            if (tr.over_time) bad.push(who + 'չի հասցնում վերադառնալ մինչև ' + endOfDay() + '-ը։');
            if (tr.over_capacity) bad.push(who + kgText(tr.kg) + ' բեռ, իսկ մեքենան տանում է մինչև ' + kgText(t.capacity_kg) + '։');
            if (tr.no_truck) bad.push(who + 'այս մեքենան այսօր նշված չէ որպես աշխատող։');
            // после правки логиста (сборка окна и центр соблюдает) — не запрет, а пометка
            if (tr.window_miss) bad.push(who + pl(tr.window_miss, 'խանութ') + ' չի հասցնում իր ընդունման ժամին։');
            if (tr.center_miss) bad.push(who + pl(tr.center_miss, 'խանութ') + ' կենտրոնում է, իսկ այս մեքենան չի կարող մտնել կենտրոն։');
            if (tr.vehicle_miss) bad.push(who + pl(tr.vehicle_miss, 'խանութ') + ' այս մեքենան չի կարող սպասարկել։');
        }));
        if (!bad.length) return;
        const div = document.createElement('div');
        div.className = 'rt-alert is-warn dp-problem';
        const notFit = plan.trucks.some(t => t.trips.some(tr => tr.over_time || tr.over_capacity || tr.no_truck));
        const winMiss = plan.trucks.some(t => t.trips.some(tr => tr.window_miss));
        const cenMiss = plan.trucks.some(t => t.trips.some(tr => tr.center_miss));
        const vehicleMiss = plan.trucks.some(t => t.trips.some(tr => tr.vehicle_miss));
        // что делать — по виду беды: не поместилось / не успевает к окну / центр на машине без права въезда
        const advice = [];
        if (notFit) advice.push('1-ին քայլում նշեք ևս մեկ մեքենա և սեղմեք «Վերակազմել երթերը», կամ սեղմեք «Փոփոխել» երթի մոտ և տեղափոխեք խանութները այլ երթ։');
        if (winMiss) advice.push('ընդունման ժամին չհասցնող խանութի մոտ սեղմեք «Փոփոխել» և տեղափոխեք այն այլ երթ կամ մեքենա, որը կհասցնի, '
            + 'կամ նշեք «Այսօր չենք տանում»՝ կտանենք հաջորդ օրը։ Եթե ժամը օրվա վերջում է, կարող եք տանել ' + state.data.work_end + '-ից հետո։');
        if (cenMiss) advice.push('կենտրոնի խանութները տեղափոխեք կենտրոն մտնող մեքենայի երթ (նշեք այդ մեքենան 1-ին քայլում)։');
        if (vehicleMiss) advice.push('խանութը տեղափոխեք թույլատրված մեքենային կամ վերակազմեք երթերը։ Ամրացված անհամապատասխան երթի մեքենան փոխեք կամ նախ ապամրացրեք այն։');
        div.innerHTML = '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i><div class="rt-alert-text"><b>'
            + (notFit ? 'Չի տեղավորվել' : 'Ուշադրություն') + '</b><ul></ul></div>';
        const ul = div.querySelector('ul');
        bad.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
        advice.forEach(t => {
            const p = document.createElement('p');
            p.className = 'dp-problem-do';
            p.textContent = 'Ինչ անել՝ ' + t;
            div.querySelector('.rt-alert-text').appendChild(p);
        });
        if (notFit || cenMiss) div.querySelector('.rt-alert-text').appendChild(stepButton('dpStep1', 'Բացել 1-ին քայլը'));
        box.appendChild(div);
    }

    // Все рейсы плана — для списка «Перенести в…»
    function tripOptions(currentTrip, placeholder, stop) {
        const opts = [['', placeholder]];
        state.data.plan.trucks.forEach(t => t.trips.forEach((tr, i) => {
            if (tr.id !== currentTrip && vehicleAllowed(stop, t.car_code)) opts.push(['t:' + tr.id, truckLabel(t) + ' · երթ ' + (i + 1)]);
        }));
        state.data.trucks.filter(t => t.selected && vehicleAllowed(stop, t.car_code)).forEach(t => opts.push(['n:' + t.car_code, 'Նոր երթ · ' + truckLabel(t)]));
        if (currentTrip !== null) opts.push(['u:', 'Հանել երթից (կմնա «դեռ երթում չէ»)']);
        return opts;
    }
    function moveSelect(stop, tripId) {
        const sel = document.createElement('select');
        sel.className = 'rt-select dp-move';
        const placeholder = tripId === null ? 'Ավելացնել երթին…' : 'Տեղափոխել այլ երթ…';
        sel.setAttribute('aria-label', placeholder + ' «' + (stop.name || stop.code) + '»');
        tripOptions(tripId, placeholder, stop).forEach(([v, t]) => sel.add(new Option(t, v)));
        sel.addEventListener('change', () => {
            const v = sel.value;
            if (!v) return;
            const body = { action: 'move', customer_id: stop.customer_id, from_trip: tripId, to_trip: null, truck: null };
            if (v.startsWith('t:')) body.to_trip = Number(v.slice(2));
            else if (v.startsWith('n:')) body.truck = v.slice(2);
            edit(body, '«' + (stop.name || stop.code) + '» տեղափոխվեց');
        });
        return sel;
    }

    // Одна точка — строка расписания: время приезда · № на линии маршрута · магазин, адрес · кг; в режиме правки — действия
    function stopItem(stop, idx, tripId, editing) {
        const li = document.createElement('li');
        li.className = 'dp-stop';
        const eta = document.createElement('span');
        eta.className = 'dp-stop-eta' + (stop.eta ? '' : ' is-none');
        if (stop.eta) eta.innerHTML = '<span class="rt-sr-only">ժամանում ≈ </span>' + esc(stop.eta);
        else { eta.textContent = '—'; eta.setAttribute('aria-hidden', 'true'); }
        const no = document.createElement('span');
        no.className = 'dp-num';
        no.setAttribute('aria-hidden', 'true');
        no.textContent = String(idx);
        const main = document.createElement('div');
        main.className = 'dp-stop-main';
        const b = document.createElement('b');
        b.textContent = stop.name || stop.code;
        const addr = document.createElement('span');
        addr.className = 'dp-stop-addr';
        addr.textContent = stop.address || 'ERP-ում հասցե չկա';
        const sub = document.createElement('span');
        sub.className = 'dp-stop-sub';
        sub.textContent = [stop.code, stop.agent_name || stop.agent_code, money(stop.revenue / (stop.share || 1))].filter(Boolean).join(' · ');
        main.append(b, addr, sub);
        // окно приёма, центр, допуск машин и источник точки — плашками; нарушение (после ручной правки) — красным
        const tags = document.createElement('span');
        tags.className = 'dp-stop-tags';
        const tag = (cls, text, ico) => {
            const bd = document.createElement('span');
            bd.className = 'rt-badge ' + cls;
            if (ico) bd.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i>';
            bd.appendChild(document.createTextNode(text));
            tags.appendChild(bd);
        };
        const win = windowText(stop.window);
        if (win) tag(stop.window_miss ? 'b-danger' : 'b-gps', (stop.window_miss ? 'չի հասցնում՝ ' : 'ընդունում է՝ ') + win, 'fa-door-open');
        // своё время у магазина (№50) — только у магазинов, где оно задано; у остальных — общая норма
        const own = ownUnload(stop);
        if (own !== null) {
            tag('dp-b-unload', 'Բեռնաթափում՝ ' + fmt(own) + NB + 'ր', 'fa-stopwatch');
            tags.lastChild.title = 'խանութի հաստատուն մասը՝ առանց բեռի ժամանակի';
        }
        if (stop.center) tag(stop.center_miss ? 'b-danger' : 'b-warn', stop.center_miss ? 'Կենտրոն — մեքենան չի կարող մտնել' : 'Կենտրոն', 'fa-city');
        if (stop.vehicle_access) tag(stop.vehicle_miss ? 'b-danger' : 'b-warn',
            (stop.vehicle_miss ? 'Մեքենան չի կարող սպասարկել · ' : '') + vehicleText(stop.vehicle_access, true), 'fa-truck');
        const src = COORD_HY[stop.coord_source];
        if (src) tag(src[0], src[1], 'fa-location-dot');
        if (tags.children.length) main.appendChild(tags);
        const kg = document.createElement('span');
        kg.className = 'dp-stop-kg';
        kg.textContent = kgText(stop.kg) + (stop.share > 1 ? ' (1/' + stop.share + ')' : '');
        li.append(eta, no, main, kg);
        if (editing) {
            const acts = document.createElement('div');
            acts.className = 'dp-stop-acts';
            const ex = document.createElement('button');
            ex.type = 'button';
            ex.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            ex.innerHTML = '<i class="fas fa-ban" aria-hidden="true"></i><span>Այսօր չենք տանում</span>';
            ex.setAttribute('aria-label', 'Այսօր չենք տանում՝ ' + (stop.name || stop.code));
            ex.addEventListener('click', () => excludeStop(stop));
            const gb = document.createElement('button');
            gb.type = 'button';
            gb.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            gb.innerHTML = '<i class="fas fa-location-dot" aria-hidden="true"></i><span>Փոխել տեղը</span>';
            gb.setAttribute('aria-label', 'Փոխել տեղը՝ ' + (stop.name || stop.code));
            gb.addEventListener('click', () => openGeo(stop));
            const vb = document.createElement('a');
            vb.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-vehiclebtn';
            vb.innerHTML = '<i class="fas fa-truck" aria-hidden="true"></i><span>Առաքման պայմաններ</span>';
            vb.setAttribute('aria-label', 'Առաքման պայմաններ՝ ' + (stop.name || stop.code));
            vb.href = '/routes/settings?customer=' + stop.customer_id + '#rsCustomerSettings';
            const ub = document.createElement('button');
            ub.type = 'button';
            ub.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-unloadbtn';
            ub.innerHTML = '<i class="fas fa-stopwatch" aria-hidden="true"></i><span>Ժամանակ խանութում</span>';
            ub.setAttribute('aria-label', 'Ժամանակ խանութում՝ ' + (stop.name || stop.code));
            ub.addEventListener('click', () => openUnload(stop));
            acts.append(moveSelect(stop, tripId), ex, vb, ub, gb);
            li.appendChild(acts);
        }
        return li;
    }

    function stopList(stops, tripId, editing) {
        const ol = document.createElement('ol');
        ol.className = 'dp-stoplist';
        stops.forEach((s, i) => ol.appendChild(stopItem(s, i + 1, tripId, editing)));
        return ol;
    }

    function renderUnassigned(plan) {
        const box = $('dpUnassigned');
        box.textContent = '';
        // Не поместились до конца рабочего дня выбранных машин (сборка за конец дня не планирует) — отдельно;
        // не успеваем в окно приёма и центр без машины с правом въезда — свои карточки (решает логист, №36)
        const noVehicle = plan.unassigned.filter(s => s.no_vehicle);
        const noRoom = plan.unassigned.filter(s => s.no_room && !s.no_vehicle);
        const noWindow = plan.unassigned.filter(s => s.no_window && !s.no_vehicle), noCenter = plan.unassigned.filter(s => s.no_center && !s.no_vehicle);
        const other = plan.unassigned.filter(s => !s.no_room && !s.no_window && !s.no_center && !s.no_vehicle);
        const kgOf = (list) => list.reduce((a, s) => a + (s.kg || 0), 0);
        const reasonCard = (list, ico, title, leads) => {
            const card = document.createElement('section');
            card.className = 'rt-card dp-unassigned';
            card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas ' + ico + '" aria-hidden="true"></i><span></span></h3>'
                + '<span class="rt-card-state is-bad"></span></div>';
            card.querySelector('.rt-card-title span').textContent = title;
            card.querySelector('.rt-card-state').textContent = pl(list.length, 'խանութ') + ', ' + kgText(kgOf(list));
            leads.filter(Boolean).forEach(t => { const p = document.createElement('p'); p.className = 'rt-card-lead'; p.textContent = t; card.appendChild(p); });
            if (leads.some(t => t && t.includes('1-ին քայլում'))) card.appendChild(stepButton('dpStep1', 'Բացել 1-ին քայլը'));
            card.appendChild(stopList(list, null, true));
            box.appendChild(card);
            return card;
        };
        // Форс-мажор (ответ владельца №32) — везти после конца дня, до предела: и «не поместились», и «не успеваем в
        // окно» (магазин «после 17:30» мог не поместиться только до конца дня — сервер пробует и его)
        const changed = trucksChanged();
        const month = state.data.overtime_days_month || 0;
        const monthText = month ? ' Այս ամիս արտաժամյա՝ ' + pl(month, 'օր') + '։' : '';
        const overtimeBlock = (text) => {
            if (state.data.overtime_ok || changed) return [];
            const p = document.createElement('p');
            p.className = 'rt-card-lead';
            p.textContent = text + ' Սա բացառություն է, ոչ թե ամենօրյա կարգ։' + monthText;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'rt-btn rt-btn-ghost dp-overtime-btn';
            btn.innerHTML = '<i class="fas fa-moon" aria-hidden="true"></i> Տանել ' + esc(state.data.work_end) + '-ից հետո';
            btn.addEventListener('click', overtime);
            return [p, btn];
        };
        if (noVehicle.length) reasonCard(noVehicle, 'fa-truck', 'Այսօր չկա խանութը սպասարկող մեքենա', [
            '1-ին քայլում միացրեք թույլատրված մեքենան և վերակազմեք երթերը։ Եթե սահմանափակումը սխալ է, փոխեք այն խանութի «Մեքենաներ» կոճակով։']);
        if (noWindow.length) {
            const card = reasonCard(noWindow, 'fa-door-closed', 'Պատուհանին չենք հասցնում', [
                'Այս խանութներին ոչ մի երթով չենք հասցնում իրենց ընդունման ժամին։ Որոշեք՝ «Այսօր չենք տանում» (կտանենք հաջորդ օրը) '
                + 'թե տանել ժամից դուրս՝ ավելացնելով որևէ երթի։ Եթե խանութի ժամը փոխվել է, ուղղեք այն «Ընդունման ժամ» կոճակով։']);
            if (!noRoom.length) {
                const list = card.querySelector('.dp-stoplist');
                overtimeBlock('Եթե խանութն ընդունում է օրվա վերջում, մեքենաները կարող են աշխատել ' + state.data.work_end
                    + '-ից հետո՝ մինչև ' + state.data.overtime_end + '-ը։').forEach(x => card.insertBefore(x, list));
            }
        }
        if (noCenter.length) {
            const can = state.data.trucks.filter(t => t.ready && t.center_ok && !t.selected).map(truckLabel);
            reasonCard(noCenter, 'fa-city', 'Կենտրոն՝ այսօր չկա թույլատրված մեքենա', [
                'Այս խանութները փոքր կենտրոնում են, իսկ այսօր նշված մեքենաներից ոչ մեկը չի կարող մտնել կենտրոն։',
                can.length ? 'Կենտրոն մտնում է՝ ' + can.join(', ') + '։ Նշեք այն 1-ին քայլում և սեղմեք «Վերակազմել երթերը»։'
                    : 'Որ մեքենաները կարող են մտնել կենտրոն, նշվում է կարգավորումներում։',
                'Կամ որոշեք ձեռքով՝ «Այսօր չենք տանում» կամ ավելացրեք որևէ երթի։']);
        }
        if (noRoom.length) {
            const card = document.createElement('section');
            card.className = 'rt-card dp-unassigned';
            card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i>Չտեղավորվեցին մինչև '
                + esc(state.data.work_end) + '-ը</h3><span class="rt-card-state is-bad"></span></div>';
            card.querySelector('.rt-card-state').textContent = pl(noRoom.length, 'խանութ') + ', ' + kgText(kgOf(noRoom));
            // Сначала — ещё машина; форс-мажор (ответ владельца №32) — везти после конца дня, до предела
            const unpicked = state.data.trucks.filter(t => t.ready && !t.selected).length;
            const lead = document.createElement('p');
            lead.className = 'rt-card-lead';
            if (state.data.overtime_ok) {
                lead.textContent = 'Մեքենաներն արդեն աշխատում են ' + state.data.work_end + '-ից հետո, բայց սրանք չեն հասցնում նույնիսկ մինչև '
                    + state.data.overtime_end + '-ը։ Նշեք «Այսօր չենք տանում»՝ դրանք կանցնեն հաջորդ օրվան։' + monthText;
            } else if (unpicked || changed) {
                lead.textContent = 'Ընտրված մեքենաները չեն հասցնի այս խանութներին առաքել մինչև ' + state.data.work_end + '-ը։ '
                    + (unpicked ? 'Կա ևս ' + pl(unpicked, 'մեքենա') + '՝ չնշված։ Նշեք 1-ին քայլում և սեղմեք «Վերակազմել երթերը»։ ' : '')
                    + 'Կամ ավելացրեք խանութը որևէ երթի ձեռքով։';
            } else {
                lead.textContent = 'Բոլոր մեքենաներն արդեն նշված են, բայց չեն հասցնում մինչև ' + state.data.work_end + '-ը։';
            }
            card.append(lead, ...overtimeBlock('Եթե այս պատվերները պետք է տանել այսօր, մեքենաները կաշխատեն ' + state.data.work_end
                + '-ից հետո՝ մինչև ' + state.data.overtime_end + '-ը։'));
            if (unpicked) card.appendChild(stepButton('dpStep1', 'Բացել 1-ին քայլը'));
            card.appendChild(stopList(noRoom, null, true));
            box.appendChild(card);
        }
        if (!other.length) return;
        const card = document.createElement('section');
        card.className = 'rt-card dp-unassigned';
        card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas fa-inbox" aria-hidden="true"></i>Դեռ ոչ մի երթում չեն</h3><span class="rt-card-state is-todo"></span></div>'
            + '<p class="rt-card-lead">Նոր կամ վերադարձված պատվերներ, կամ խանութներ, որոնց տեղը նոր եք նշել։ Յուրաքանչյուրի համար ընտրեք՝ որ երթին ավելացնել, '
            + 'կամ պարզապես սեղմեք «Վերակազմել երթերը»։</p>';
        card.querySelector('.rt-card-state').textContent = pl(other.length, 'խանութ') + ', ' + kgText(kgOf(other));
        card.appendChild(stopList(other, null, true));
        box.appendChild(card);
    }

    // Карточка машины: шапка — кнопка «раскрыть» (итог и время возвращения), внутри — рейсы со списком точек
    function renderTruckCards(plan) {
        const box = $('dpTruckCards');
        box.textContent = '';
        plan.trucks.forEach((t, k) => {
            const color = truckColor(t.car_code);
            const open = state.open.has(t.car_code) || t.trips.some(tr => state.editing.has(tr.id));
            const card = document.createElement('section');
            card.className = 'rt-card dp-tcard';
            card.dataset.truck = t.car_code;
            card.style.setProperty('--dp-c', color);
            const h = document.createElement('h3');
            h.className = 'dp-thead-h';
            const head = document.createElement('button');
            head.type = 'button';
            head.className = 'dp-thead';
            head.id = 'dpTH' + k;
            head.setAttribute('aria-expanded', String(open));
            head.setAttribute('aria-controls', 'dpTB' + k);
            head.innerHTML = '<span class="dp-thead-t"><span class="dp-thead-name"><b></b></span><span class="dp-tstats"></span></span>'
                + '<span class="dp-tret"><small>վերադարձ</small><b></b></span>'
                + '<span class="dp-chev" aria-hidden="true"><i class="fas fa-chevron-down"></i></span>';
            head.querySelector('.dp-thead-name b').textContent = t.name || t.car_code;
            if (t.name) {
                const plate = document.createElement('span');
                plate.className = 'dp-plate';
                plate.textContent = t.car_code;
                head.querySelector('.dp-thead-name').appendChild(plate);
            }
            const st = head.querySelector('.dp-tstats');
            st.textContent = pl(t.trips.length, 'երթ') + ' · ' + pl(t.stops, 'խանութ') + ' · ' + kgText(t.kg) + ' · ≈ ' + fmt(t.km) + NB + 'կմ'
                + (num(t.liters) !== null ? ' · ≈ ' + fmt(t.liters, 1) + NB + 'լ' : '');
            if (t.over_time) st.classList.add('is-bad');
            const ret = head.querySelector('.dp-tret b');
            ret.textContent = t.return;
            if (t.over_time) ret.className = 'is-bad'; else if (t.late) ret.className = 'is-late';
            head.addEventListener('click', () => toggleTruck(t.car_code));
            h.appendChild(head);
            const body = document.createElement('div');
            body.className = 'dp-tbody';
            body.id = 'dpTB' + k;
            body.setAttribute('role', 'region');
            body.setAttribute('aria-labelledby', head.id);
            body.hidden = !open;
            if (open) t.trips.forEach((tr, i) => body.appendChild(tripBlock(t, tr, i)));
            card.append(h, body);
            box.appendChild(card);
        });
        syncFocus();
    }
    function toggleTruck(code) {
        const t = state.data.plan.trucks.find(x => x.car_code === code);
        const shown = state.open.has(code) || (!!t && t.trips.some(tr => state.editing.has(tr.id)));
        if (shown) { state.open.delete(code); if (t) t.trips.forEach(tr => state.editing.delete(tr.id)); } else state.open.add(code);
        renderTruckCards(state.data.plan);
        const card = [...$('dpTruckCards').querySelectorAll('.dp-tcard')].find(c => c.dataset.truck === code);
        if (card) card.querySelector('.dp-thead').focus();
    }

    function tripBlock(t, tr, i) {
        const editing = state.editing.has(tr.id);
        const div = document.createElement('div');
        div.className = 'dp-trip' + (tr.over_time || tr.over_capacity || tr.vehicle_miss ? ' is-bad' : '') + (editing ? ' is-editing' : '');
        div.dataset.trip = String(tr.id);
        const head = document.createElement('div');
        head.className = 'dp-trip-head';
        const title = document.createElement('h4');
        title.className = 'dp-trip-t';
        title.textContent = 'Երթ ' + (i + 1);
        // время: погрузка · отъезд → возвращение (подписи «մեկնում / վերադարձ» — для экранного диктора)
        const time = document.createElement('span');
        time.className = 'dp-time';
        if (tr.loading_minutes) time.insertAdjacentHTML('beforeend', '<small>բեռնում ' + esc(tr.loading_start) + ' ·</small>');
        time.insertAdjacentHTML('beforeend', '<span class="rt-sr-only">մեկնում </span>' + esc(tr.depart)
            + '<span class="dp-arrow" aria-hidden="true">→</span><span class="rt-sr-only"> վերադարձ </span>' + esc(tr.return));
        const acts = document.createElement('div');
        acts.className = 'dp-trip-acts';
        const onMap = document.createElement('button');
        onMap.type = 'button';
        onMap.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-mapbtn';
        onMap.innerHTML = '<i class="fas fa-map-location-dot" aria-hidden="true"></i><span>Քարտեզում</span>';
        onMap.setAttribute('aria-label', 'Ցույց տալ քարտեզում՝ ' + truckLabel(t) + ', երթ ' + (i + 1));
        onMap.addEventListener('click', () => showTripOnMap(t, tr));
        const tog = document.createElement('button');
        tog.type = 'button';
        tog.className = 'rt-btn rt-btn-sm dp-editbtn ' + (editing ? 'rt-btn-primary' : 'rt-btn-ghost');
        tog.dataset.trip = String(tr.id);
        tog.setAttribute('aria-expanded', String(editing));
        tog.innerHTML = '<i class="fas ' + (editing ? 'fa-check' : 'fa-pen') + '" aria-hidden="true"></i><span></span>';
        tog.lastChild.textContent = editing ? 'Պատրաստ է' : 'Փոփոխել';
        tog.setAttribute('aria-label', (editing ? 'Ավարտել փոփոխությունը՝ ' : 'Փոփոխել՝ ') + truckLabel(t) + ', երթ ' + (i + 1));
        tog.addEventListener('click', () => toggleEdit(tr.id));
        acts.append(onMap, tog);
        // строка под заголовком: загрузка машины полоской, км, литры, пометки
        const line = document.createElement('div');
        line.className = 'dp-trip-line';
        const pct = num(tr.load_pct);
        const load = document.createElement('span');
        load.className = 'dp-loadm' + (tr.over_capacity || (pct !== null && pct > 100) ? ' is-over' : pct !== null && pct >= 90 ? ' is-high' : '');
        if (pct !== null) {
            load.innerHTML = '<span class="dp-loadm-bar" aria-hidden="true"><i></i></span>';
            load.querySelector('i').style.width = Math.min(100, Math.max(0, pct)) + '%';
        }
        load.appendChild(document.createTextNode(kgText(tr.kg) + (pct !== null ? ' · մեքենան լցված է ' + pct + '%-ով' : '')));
        const meta = document.createElement('span');
        meta.className = 'dp-trip-meta';
        meta.textContent = '≈ ' + fmt(tr.km) + NB + 'կմ' + (tr.liters !== null ? ' · ≈ ' + fmt(tr.liters, 1) + NB + 'լ' : '')
            + (tr.wear_configured ? ' · մաշվածք՝ ≈ ' + money(tr.wear_amd) : '');
        const flags = document.createElement('span');
        flags.className = 'dp-trip-flags';
        if (tr.over_time) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">ուշանում է</span>');
        else if (tr.late) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn"><i class="fas fa-moon" aria-hidden="true"></i>արտաժամյա</span>');
        if (tr.over_capacity) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">գերբեռնված</span>');
        if (tr.window_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">ընդունման ժամից դուրս՝ ' + esc(fmt(tr.window_miss)) + '</span>');
        if (tr.center_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">կենտրոն՝ առանց թույլտվության</span>');
        if (tr.vehicle_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">մեքենան չի կարող սպասարկել՝ ' + esc(fmt(tr.vehicle_miss)) + '</span>');
        if (tr.poor) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn">' + esc(fmt(state.data.min_trip_revenue)) + NB + 'դրամից պակաս</span>');
        if (tr.pinned) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-ok"><i class="fas fa-lock" aria-hidden="true"></i>ամրացված</span>');
        line.append(load, meta, flags);
        head.append(title, time, acts, line);
        div.appendChild(head);
        if (tr.poor && !state.data.is_past) div.appendChild(poorNote(tr));
        if (editing) div.appendChild(tripTools(t, tr, i));
        div.appendChild(stopList(tr.stops, tr.id, editing));
        return div;
    }

    // Рейс дешевле порога (ответ владельца №25): везти сейчас или завтра — решает логист
    function poorNote(tr) {
        const box = document.createElement('div');
        box.className = 'dp-poor';
        const p = document.createElement('p');
        p.textContent = 'Երթի ապրանքը՝ ' + fmt(tr.revenue) + NB + 'դրամ, ' + fmt(state.data.min_trip_revenue) + NB + 'դրամից պակաս։ '
            + 'Կարող եք տանել ' + dayHuman(state.data.defer_to, true) + '՝ այլ երթերի հետ։ Այսօր կխնայվի մինչև ≈ ' + fmt(tr.km) + NB + 'կմ'
            + (tr.liters !== null ? ' (' + fmt(tr.liters, 1) + NB + 'լ)' : '') + ', իսկ '
            + pl(tr.stops.length, 'խանութ') + ' կստանա առաքումը ավելի ուշ։';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        btn.innerHTML = '<i class="fas fa-calendar-plus" aria-hidden="true"></i><span></span>';
        btn.lastChild.textContent = 'Տանել ' + dayHuman(state.data.defer_to, true);
        btn.addEventListener('click', () => {
            if (!window.confirm(pl(tr.stops.length, 'խանութ') + ' կստանա առաքումը ' + dayHuman(state.data.defer_to, true) + '։ Շարունակե՞լ։')) return;
            edit({ action: 'defer_trip', trip: tr.id }, 'Երթը տեղափոխվեց վաղվան');
        });
        box.append(p, btn);
        return box;
    }

    // Правка рейса: машина и «закрепить» — с пояснением, что это значит
    function tripTools(t, tr, i) {
        const tools = document.createElement('div');
        tools.className = 'dp-trip-tools';
        const lab = document.createElement('label');
        lab.className = 'dp-trip-truck';
        lab.textContent = 'Մեքենան՝ ';
        const sel = document.createElement('select');
        sel.className = 'rt-select';
        sel.setAttribute('aria-label', 'Երթ ' + (i + 1) + '-ի մեքենան');
        const working = state.data.trucks.filter(x => x.selected && tr.stops.every(s => vehicleAllowed(s, x.car_code)));
        if (!working.some(x => x.car_code === t.car_code)) sel.add(new Option('Ընտրեք մեքենա…', '', true, true));
        working.forEach(x => sel.add(new Option(truckLabel(x), x.car_code, false, x.car_code === t.car_code)));
        sel.addEventListener('change', () => sel.value && edit({ action: 'pin', trip: tr.id, truck: sel.value }, 'Երթը տրվեց մեքենային՝ ' + truckLabel(truckBy(sel.value))));
        lab.appendChild(sel);
        const pin = document.createElement('button');
        pin.type = 'button';
        pin.disabled = !tr.pinned && !!tr.vehicle_miss;
        pin.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        pin.setAttribute('aria-pressed', String(!!tr.pinned));
        pin.innerHTML = '<i class="fas ' + (tr.pinned ? 'fa-lock-open' : 'fa-lock') + '" aria-hidden="true"></i><span></span>';
        pin.lastChild.textContent = tr.pinned ? 'Ապամրացնել' : 'Ամրացնել երթը';
        pin.addEventListener('click', () => edit(tr.pinned ? { action: 'unpin', trip: tr.id } : { action: 'pin', trip: tr.id, truck: t.car_code },
            tr.pinned ? 'Երթն ապամրացվեց' : 'Երթն ամրացվեց մեքենային՝ ' + truckLabel(t)));
        const hint = document.createElement('p');
        hint.className = 'dp-tools-hint';
        hint.textContent = (tr.pinned
            ? 'Երթն ամրացված է՝ «Վերակազմել երթերը» սեղմելիս այն չի փոխվի։'
            : '«Ամրացնել»՝ որպեսզի «Վերակազմել երթերը» սեղմելիս այս երթը մնա ինչպես կա։')
            + ' Խանութը այլ երթ տեղափոխելու կամ այսօր չտանելու համար օգտվեք նրա տողի կոճակներից։';
        tools.append(lab, pin, hint);
        return tools;
    }

    function toggleEdit(tripId) {
        if (state.editing.has(tripId)) state.editing.delete(tripId); else state.editing.add(tripId);
        const t = state.data.plan.trucks.find(x => x.trips.some(tr => tr.id === tripId));
        if (t) state.open.add(t.car_code);      // правка — внутри раскрытой карточки; «Պատրաստ է» её не сворачивает
        renderTruckCards(state.data.plan);
        const b = $('dpTruckCards').querySelector('.dp-editbtn[data-trip="' + tripId + '"]');
        if (b) b.focus();
    }

    function renderBaseline(plan) {
        const b = plan.baseline;
        $('dpBaseline').hidden = !b;
        if (!b) return;
        $('dpBaselineNote').textContent = '≈ ' + fmt(b.km) + NB + 'կմ, ' + fmt(b.liters) + NB + 'լ, ' + pl(b.trips, 'երթ');
        const tb = $('dpBaselineRows');
        tb.textContent = '';
        b.trucks.forEach(r => {
            const tr = document.createElement('tr');
            [truckLabel(truckBy(r.car_code)) + (r.over_time ? ' — չի հասցնում մինչև ' + endOfDay() + '-ը' : ''), fmt(r.stops), fmt(r.kg), fmt(r.trips), fmt(r.km, 1)].forEach((v, i) => {
                const td = document.createElement('td');
                td.textContent = v;
                td.setAttribute('data-label', ['Մեքենա', 'Կետեր', 'կգ', 'Երթեր', 'կմ'][i]);
                if (i) td.className = 'num w-half';
                tr.appendChild(td);
            });
            tb.appendChild(tr);
        });
    }

    // ---------- Карта ----------
    const MAP_FIT = { padding: [24, 24], maxZoom: 14, animate: false };
    // Вписать точки программно — не считается, что логист двигал карту
    function fitMap(map, b) {
        state.mapFitting = true;
        try { map.fitBounds(b, MAP_FIT); } finally { state.mapFitting = false; }
    }
    // Leaflet не замечает, что его блок стал видимым или сменил размер (свёртка, скрытый шаг, поворот телефона):
    // тогда грузит одну плитку и центрирует не туда. Следим за размером сами; из 0×0 в видимый — снова вписать точки.
    function watchSize(el, map, bounds) {
        if (typeof window.ResizeObserver === 'undefined') return;
        let wasEmpty = el.clientWidth === 0 || el.clientHeight === 0;
        new ResizeObserver(() => {
            const empty = el.clientWidth === 0 || el.clientHeight === 0;
            if (empty) { wasEmpty = true; return; }
            map.invalidateSize({ pan: false });
            const b = bounds();
            if (wasEmpty && b) fitMap(map, b);
            wasEmpty = false;
        }).observe(el);
    }

    function ensureMap() {
        if (state.map || state.mapFailed) return;
        const el = $('dpMap');
        if (typeof window.L === 'undefined') {
            state.mapFailed = true;
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Երթերը, տպումը և Excel-ը աշխատում են։';
            return;
        }
        const map = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false });
        map.on('movestart zoomstart', () => { if (!state.mapFitting) state.mapUserMoved = true; });
        RoutesBasemap.add(map);
        map.setView(YEREVAN, 10);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.layers = L.layerGroup().addTo(map);
        watchSize(el, map, () => state.mapBounds);
    }
    // Над картой: «Բոլորը», каждая машина и каждый её рейс (ответ владельца №42) — выбранное одно на карте
    function mapFilter(plan) {
        const box = $('dpLegend');
        box.textContent = '';
        const f = state.mapFocus;
        const pill = (text, pressed, aria, onClick, color) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-lg';
            b.setAttribute('aria-pressed', String(pressed));
            if (aria) b.setAttribute('aria-label', aria);
            if (color) {
                const dot = document.createElement('span');
                dot.className = 'rt-dot';
                dot.setAttribute('aria-hidden', 'true');
                dot.style.background = color;
                b.appendChild(dot);
            }
            const n = document.createElement('span');
            n.className = 'n';
            n.textContent = text;
            b.appendChild(n);
            // кнопки строятся заново — фокус клавиатуры вернуть на ту же кнопку
            b.addEventListener('click', () => {
                onClick();
                const again = [...box.querySelectorAll('button')].find(x => x.getAttribute('aria-label') === aria);
                if (again) again.focus();
            });
            box.appendChild(b);
        };
        pill('Բոլորը', !f, 'Բոլոր մեքենաները քարտեզում', () => setMapFocus(null));
        plan.trucks.forEach(t => {
            const color = truckColor(t.car_code);
            const one = t.trips.length === 1;
            pill(truckLabel(t) + ' — ' + pl(t.trips.length, 'երթ'),
                 !!f && f.truck === t.car_code && (f.trip == null || one), 'Միայն ' + truckLabel(t),
                 () => setMapFocus({ truck: t.car_code, trip: null }), color);
            if (!one) t.trips.forEach((tr, i) => pill('երթ ' + (i + 1), !!f && f.trip === tr.id,
                'Միայն ' + truckLabel(t) + ', երթ ' + (i + 1), () => setMapFocus({ truck: t.car_code, trip: tr.id }), color));
        });
        if (plan.unassigned.length && !f) {
            const lg = document.createElement('span');
            lg.className = 'dp-lg';
            lg.innerHTML = '<span class="rt-dot dp-dot-open" aria-hidden="true"></span><span></span>';
            lg.lastChild.textContent = 'դեռ երթում չէ՝ ' + plan.unassigned.length;
            box.appendChild(lg);
        }
        // что выбрано — одной строкой: время, точки, кг, км; под картой — «почему так» (renderWhy)
        const note = $('dpMapFocus');
        const t = f && plan.trucks.find(x => x.car_code === f.truck);
        note.hidden = !t;
        const i = !t ? -1 : f.trip == null ? (t.trips.length === 1 ? 0 : -1) : t.trips.findIndex(tr => tr.id === f.trip);
        renderWhy(plan, t, i);
        if (!t) return;
        if (i >= 0) {
            const tr = t.trips[i];
            note.textContent = truckLabel(t) + ' · երթ ' + (i + 1) + ' · մեկնում ' + tr.depart + ' → վերադարձ ' + tr.return + ' · '
                + pl(tr.stops.length, 'կետ') + ' · ' + kgText(tr.kg) + ' · ≈ ' + fmt(tr.km) + NB + 'կմ։ Թվերը՝ այցելության հերթականությունը։';
        } else {
            note.textContent = truckLabel(t) + ' · ' + pl(t.trips.length, 'երթ') + ' · ' + pl(t.stops, 'կետ') + ' · ≈ ' + fmt(t.km) + NB
                + 'կմ, վերադարձ ' + t.return + '։ «2.3»՝ 2-րդ երթի 3-րդ կետը։';
        }
    }
    function setMapFocus(f) {
        state.mapFocus = f;
        drawMap();
        syncFocus();
    }
    // Кнопка «Քարտեզում» у рейса: открыть карту только с этим рейсом и прокрутить к ней
    function showTripOnMap(t, tr) {
        state.mapFocus = { truck: t.car_code, trip: tr.id };
        const box = $('dpMapBox');
        if (box.open) drawMap(); else box.open = true;   // открытие само перерисует карту (toggle)
        syncFocus();
        // карта закреплена рядом с рейсами и видна — не прокручивать; иначе (узкий экран, карта ниже рейсов) — к карте
        const r = $('dpMap').getBoundingClientRect();
        if (!box.open || r.top < 0 || r.top > window.innerHeight - 160) box.scrollIntoView({ behavior: calm() ? 'auto' : 'smooth', block: 'start' });
    }

    // ---------- «Почему так» под картой выбранного (ответ владельца №49) ----------
    // Сервер даёт только цифры того же расчёта (plan_view: explain у рейса и у дня; у точки — приезд, минуты езды от
    // предыдущей, ожидания окна, разгрузки и запас до конца окна); слова — здесь, короткими фразами. Чего расчёт не знает —
    // не пишем.
    const KM_SRC = {
        osm: 'ըստ ճանապարհային քարտեզի (OpenStreetMap)՝ հաշվի առնելով միակողմանի երթևեկությունը',
        valhalla: 'ըստ Valhalla-ի ճանապարհների՝ բեռնատարի համար',
        yandex: 'ըստ Յանդեքսի ճանապարհների',
    };
    const SPEED_SRC = { gps: 'մենեջերների GPS-ից', manual: 'կարգավորումներից', default: 'լռելյայն արժեք' };
    const REASON_HY = {
        capacity: (o) => 'փոքր է, տանում է մինչև ' + kgText(o.capacity_kg),
        load_cap: (o, cap) => 'բեռնվածությունը կլիներ ' + o.load_pct + '%, իսկ կանոնը՝ առավելագույնը ' + cap + '%',
        center: () => 'կենտրոն չի մտնում',
        vehicle: (o) => pl(o.vehicle_denied, 'խանութ') + ' այն չի ընդունում',
    };
    const capFirst = (s) => s.charAt(0).toUpperCase() + s.slice(1);
    const storeName = (s) => '«' + (s.name || s.code) + '»';
    const whyOpen = {};   // раскрытые подробности (ключ → открыто): переживают перерисовку карты
    function minText(m) {
        const v = Math.max(0, Math.round(num(m) || 0)), h = Math.floor(v / 60), r = v % 60;
        return h ? h + NB + 'ժ' + (r ? ' ' + r + NB + 'րոպե' : '') : r + NB + 'րոպե';
    }
    // «09:00» / «04:06 (+1)» → минуты от полуночи дня доставки; не время — null
    function clockMin(s) {
        const m = /^(\d{2}):(\d{2})(?: \(\+(\d+)\))?$/.exec(String(s || ''));
        return m ? Number(m[3] || 0) * 1440 + Number(m[1]) * 60 + Number(m[2]) : null;
    }
    // Слагаемые в целых минутах с суммой ровно total (как «выезд → возвращение» на часах): вниз до целых, недостающие
    // минуты — слагаемым с большими остатками, лишние — снимаются с меньших
    function roundParts(values, total) {
        const v = values.map(x => Math.max(0, num(x) || 0)), out = v.map(Math.floor);
        let left = total - out.reduce((a, b) => a + b, 0);
        const order = v.map((x, k) => [x - Math.floor(x), k]).sort((a, b) => b[0] - a[0]).map(p => p[1]);
        for (let n = 0; left > 0 && n < order.length; n++, left--) out[order[n]]++;
        for (let n = order.length - 1; left < 0 && n >= 0; n--) if (out[order[n]] > 0) { out[order[n]]--; left++; }
        return out;
    }
    // Минуты рейса: загрузка, в пути, разгрузка, ожидание окон — целыми, в сумме ровно «загрузка → возвращение» по часам
    function tripTime(tr) {
        const x = tr.explain, begin = clockMin(tr.loading_start), back = clockMin(tr.return);
        const total = begin !== null && back !== null ? back - begin : Math.round(x.loading_min + x.drive_min + x.unload_min + x.wait_min);
        const [loading, drive, unload, wait] = roundParts([x.loading_min, x.drive_min, x.unload_min, x.wait_min], total);
        return { total, loading, drive, unload, wait };
    }
    // Км по прямой бывают и с картой: карта не загрузилась — пишем, что её нет; иначе — что объезд не посчитан
    const noBypass = (model) => (model.km === 'straight' ? 'ճանապարհային քարտեզը հասանելի չէ' : 'շրջանցման հաշվարկը հասանելի չէ');
    function kmSource(model) {
        if (model.km === 'straight') return 'ուղիղ գծով × ' + fmt(model.detour, 2) + '՝ ճանապարհային քարտեզը հասանելի չէ';
        return KM_SRC[model.km] || '';
    }
    function whyFold(key, title, openByDefault) {
        const d = document.createElement('details');
        d.className = 'dp-why-fold';
        d.open = Object.prototype.hasOwnProperty.call(whyOpen, key) ? whyOpen[key] : openByDefault;
        d.addEventListener('toggle', () => { whyOpen[key] = d.open; });
        const s = document.createElement('summary');
        s.textContent = title;
        d.appendChild(s);
        return d;
    }
    // Разделы: [заголовок, [фраза, …]] — пустые фразы и разделы без фраз пропускаются
    function whyList(rows) {
        const dl = document.createElement('dl');
        dl.className = 'dp-why-list';
        rows.forEach(([k, lines]) => {
            const ls = lines.filter(Boolean);
            if (!ls.length) return;
            const row = document.createElement('div');
            row.className = 'dp-why-row';
            const dt = document.createElement('dt');
            dt.textContent = k;
            const dd = document.createElement('dd');
            ls.forEach(t => { const p = document.createElement('p'); p.textContent = t; dd.appendChild(p); });
            row.append(dt, dd);
            dl.appendChild(row);
        });
        return dl;
    }
    function whyTitle(text) {
        const h = document.createElement('h3');
        h.className = 'dp-why-t';
        h.textContent = text;
        return h;
    }
    function renderWhy(plan, t, i) {
        const box = $('dpWhy');
        box.textContent = '';
        box.hidden = !plan.explain;   // ответ сервера без пояснения — панели нет
        if (!plan.explain) return;
        if (!t) box.appendChild(whyDay(plan));
        else if (i >= 0) box.append(...whyTrip(plan, t, t.trips[i], i));
        else box.appendChild(whyTruck(t));
    }
    // Другая машина дня на этот же рейс: может ли (тоннаж, 90%, центр, допуск магазина) и сколько литров вышло бы
    function otherText(plan, tr, o, cap) {
        if (o.reasons.length) return truckLabel(o) + ' — չի կարող (' + o.reasons.map(r => (REASON_HY[r] ? REASON_HY[r](o, cap) : r)).join('; ') + ')։';
        const d = num(o.liters) !== null && num(tr.liters) !== null ? o.liters - tr.liters : null;
        const diff = d === null ? '' : Math.abs(d) < 0.05 ? ' (նույնքան)' : ' (' + fmt(Math.abs(d), 1) + NB + 'լ-ով ' + (d < 0 ? 'պակաս' : 'ավելի') + ')';
        const own = plan.trucks.find(p => p.car_code === o.car_code);
        const n = own ? own.trips.length : 0;
        return truckLabel(o) + ' — կարող էր տանել ≈ ' + fmt(o.liters, 1) + NB + 'լ դիզելով' + diff + '։ '
            + (n ? 'Այս օրն այն արդեն ունի ' + pl(n, 'երթ') + '։' : 'Այս օրն այն երթ չունի։');
    }
    function whyTrip(plan, t, tr, i) {
        const x = tr.explain, day = plan.explain, model = day.model || {}, d = state.data;
        const truck = [truckLabel(t) + (num(t.capacity_kg) === null ? '՝ այս մեքենան այսօր նշված չէ որպես աշխատող։'
            : '՝ տանում է մինչև ' + kgText(t.capacity_kg) + ', ' + (t.center_ok ? 'մտնում է կենտրոն' : 'կենտրոն չի մտնում') + '։')];
        if (tr.pinned) truck.push('Երթն ամրացված է՝ «Վերակազմել երթերը» սեղմելիս այն չի փոխվի։');
        const ruled = tr.stops.filter(s => s.vehicle_access);
        if (ruled.length) {
            const bad = ruled.filter(s => s.vehicle_miss).length;
            truck.push(pl(ruled.length, 'խանութ') + ' ունի մեքենաների սահմանափակում (' + ruled.slice(0, 3).map(storeName).join(', ')
                + (ruled.length > 3 ? ' և ևս ' + (ruled.length - 3) : '') + ')՝ '
                + (bad ? 'այս մեքենան թույլատրված չէ ' + pl(bad, 'խանութում') + '։' : 'այս մեքենան թույլատրված է։'));
        }
        if (!x.others.length) truck.push('Այս օրը նշված է միայն այս մեքենան։');
        else {
            truck.push('Մյուս նշված մեքենաները՝ նույն կետերով, նույն հերթականությամբ՝');
            x.others.forEach(o => truck.push('• ' + otherText(plan, tr, o, x.load_cap_pct)));
            truck.push('Ծրագիրը կազմում է ամբողջ օրվա երթերը միասին, ոչ թե յուրաքանչյուր երթն առանձին․ մյուս մեքենան կարող է զբաղված լինել իր երթերով։');
        }
        const load = [kgText(tr.kg) + (tr.load_pct !== null ? '՝ մեքենան լցված է ' + tr.load_pct + '%-ով' : '') + ' (կանոնը՝ առավելագույնը ' + x.load_cap_pct + '%)։'];
        if (tr.over_capacity) load.push('Բեռը մեքենայի տոննաժից ավելի է։');
        else if (x.heavy_alone) load.push('Պատվերն ինքնին ծանր է ամենամեծ հարմար մեքենայի տոննաժի ' + x.load_cap_pct + '%-ից, ուստի գնում է առանձին երթով՝ կանոնը դա թույլ է տալիս։');
        else if (x.over_limit) load.push('Բեռը ' + x.load_cap_pct + '%-ից ավելի է։');
        tr.stops.filter(s => s.share > 1).forEach(s => load.push(storeName(s) + '-ի պատվերը մեծ է և տարվում է ' + s.share + ' երթով, այստեղ՝ 1/' + s.share + ' մասը։'));
        const src = kmSource(model);
        const road = ['≈ ' + fmt(tr.km, 1) + NB + 'կմ' + (src ? '՝ ' + src : '') + '։ Կետերի հերթականությունը՝ ինչպես քարտեզի թվերը։'];
        const tt = tripTime(tr), bits = [];
        if (tt.loading) bits.push('բեռնում պահեստում՝ ' + minText(tt.loading));
        bits.push('ճանապարհին՝ ' + minText(tt.drive), 'բեռնաթափում՝ ' + minText(tt.unload));
        if (tt.wait) bits.push('ընդունման ժամի սպասում՝ ' + minText(tt.wait));
        const time = [capFirst(bits.join(', ')) + '։ Ընդամենը՝ ' + minText(tt.total) + ' (' + tr.loading_start + ' → ' + tr.return + ')։'];
        if (x.idle_before_min >= 1) time.push((tt.loading ? 'Բեռնումը սկսվում է ' + tr.loading_start : 'Մեքենան մեկնում է ' + tr.depart)
            + '-ին, ոչ ավելի շուտ, որպեսզի ' + (tt.loading ? 'մեքենան ' : '') + 'առաջին խանութ հասնի դրա ընդունման ժամի սկզբին և չսպասի։');
        if (i < t.trips.length - 1) time.push('Դրանից հետո նույն մեքենան կատարում է ' + (i + 2) + '-րդ երթը։');
        else if (x.end_slack_min >= -0.5) time.push('Մինչև աշխատանքային օրվա ավարտը (' + d.work_end + ') մնում է ' + minText(x.end_slack_min) + '։');
        else time.push('Վերադառնում է աշխատանքային օրվա ավարտից (' + d.work_end + ') ' + minText(-x.end_slack_min) + ' ուշ'
            + (d.overtime_ok ? '․ արտաժամյա աշխատանքը թույլատրված է մինչև ' + d.overtime_end + '-ը' : '') + '։');
        const fuel = [];
        if (tr.liters !== null) {
            fuel.push(tr.fuel_load_configured && x.fuel_empty_l100 !== null
                ? '≈ ' + fmt(tr.liters, 1) + NB + 'լ՝ դատարկ մեքենան ծախսում է ' + fmt(x.fuel_empty_l100, 1) + NB + 'լ/100' + NB + 'կմ, լրիվ բեռնված՝ '
                    + fmt(x.fuel_full_l100, 1) + NB + 'լ/100' + NB + 'կմ, հաշվարկված է ըստ յուրաքանչյուր հատվածում մնացած բեռի։'
                : '≈ ' + fmt(tr.liters, 1) + NB + 'լ = ' + fmt(tr.km, 1) + NB + 'կմ × ' + fmt(x.l100, 1) + NB + 'լ/100' + NB + 'կմ։');
            if (model.learned && (model.learned.fuel || []).includes(t.car_code)) fuel.push('Նորմը սովորած է այս մեքենայի լիցքավորումներից։');
        }
        const center = [];
        if (!day.zone) center.push('Փոքր կենտրոնի սահմանը կարգավորումներում նշված չէ։');
        else {
            const n = tr.stops.filter(s => s.center).length;
            center.push(t.center_ok ? 'Մեքենան կարող է մտնել կենտրոն՝ ' + (n ? 'երթում կենտրոնի ' + pl(n, 'խանութ') + ' կա։' : 'երթում կենտրոնի խանութ չկա։')
                : n ? 'Երթում կենտրոնի ' + pl(n, 'խանութ') + ' կա, իսկ այս մեքենան չի կարող մտնել կենտրոն։' : 'Մեքենան կենտրոն չի մտնում, և երթում կենտրոնի խանութ չկա։');
            if (model.bypass === false) center.push('Կենտրոնի շրջանցումը չի հաշվվել՝ ' + noBypass(model) + '։');
            else if (x.bypass_legs) center.push('Կենտրոնից դուրս կետերի միջև ճանապարհը շրջանցում է կենտրոնը՝ +' + fmt(x.bypass_km, 1) + NB + 'կմ ('
                + pl(x.bypass_legs, 'հատված') + ')։ Առանց շրջանցման կլիներ ≈ ' + fmt(tr.km - x.bypass_km, 1) + NB + 'կմ։');
            else if (model.bypass) center.push('Կենտրոնից դուրս կետերի միջև ճանապարհը շրջանցում է կենտրոնը՝ այս երթում դա կիլոմետր չի ավելացրել։');
        }
        const win = tr.stops.filter(s => windowText(s.window));
        const windows = [];
        if (!win.length) windows.push('Այս երթի խանութներից ոչ մեկն ընդունման ժամ չունի։');
        else {
            const miss = win.filter(s => s.window_miss).length;
            windows.push(pl(win.length, 'խանութ') + ' ունի ընդունման ժամ՝ '
                + (miss ? 'դրանցից ' + pl(miss, 'խանութ') + ' ժամանակին չենք հասնում։' : 'բոլորին հասնում ենք ժամանակին։'));
            const tight = win.filter(s => !s.window_miss && num(s.margin_min) !== null).sort((p, q) => p.margin_min - q.margin_min)[0];
            if (tight) windows.push('Ամենաքիչ պաշարը ' + storeName(tight) + '-ում է՝ ' + minText(tight.margin_min) + ' (' + windowText(tight.window)
                + ', բեռնաթափումը սկսվում է ≈ ' + tight.eta + ')։');
            const waited = tr.stops.filter(s => s.wait_min >= 0.5);
            if (waited.length) windows.push('Մեքենան շուտ է հասնում և սպասում է․ ' + waited.map(s => storeName(s) + ' — ' + minText(s.wait_min)).join(', ') + '։');
        }
        const cnt = {};
        tr.stops.forEach(s => { cnt[s.coord_source] = (cnt[s.coord_source] || 0) + 1; });
        const quality = ['Խանութների կետերի աղբյուրը․ ' + ['manual', 'driver', 'erp', 'gps'].filter(k => cnt[k]).map(k => COORD_HY[k][1] + ' — ' + cnt[k]).join(', ') + '։'];
        const title = 'Ինչո՞ւ է այս երթն այսպիսին';
        const box = document.createElement('section');
        box.setAttribute('aria-label', title);
        box.append(whyTitle(title), whyList([['Մեքենա', truck], ['Բեռ', load], ['Ճանապարհ', road], ['Ժամանակ', time],
            ['Դիզել', fuel], ['Կենտրոն', center], ['Ընդունման ժամեր', windows], ['Տվյալների որակ', quality]]));
        return [box, whyStops(tr, tr.explain)];
    }
    // Время по точкам. «Ժամանում» — когда машина подъехала (arrive); ждёт окна — «Սպասում» до начала разгрузки, это и есть
    // eta в листе рейса («ժամանում ≈ …» у точки — начало разгрузки, по нему проверяется окно приёма)
    function whyStops(tr, x) {
        const fold = whyFold('stops:' + tr.id, 'Ժամանակն ըստ կետերի', false);
        const body = document.createElement('div');
        body.className = 'dp-why-body';
        if (x.loading_min >= 0.5) {
            const p = document.createElement('p');
            p.textContent = 'Բեռնում պահեստում՝ ' + tr.loading_start + ' → ' + tr.depart + ' (' + minText(x.loading_min) + ')։';
            body.appendChild(p);
        }
        const heads = ['Խանութ', 'Հասնում է', 'Ճանապարհ, րոպե', 'Սպասում, րոպե', 'Բեռնաթափում, րոպե', 'Ընդունման ժամ'];
        const table = document.createElement('table');
        table.className = 'dp-why-table';
        table.innerHTML = '<thead><tr></tr></thead><tbody></tbody>';
        heads.forEach(h => { const th = document.createElement('th'); th.scope = 'col'; th.textContent = h; table.querySelector('tr').appendChild(th); });
        const row = (cells) => {
            const r = document.createElement('tr');
            cells.forEach((v, k) => {
                const td = document.createElement('td');
                td.textContent = v;
                if (k && k < heads.length - 1) td.className = 'num';
                r.appendChild(td);
            });
            table.tBodies[0].appendChild(r);
        };
        tr.stops.forEach((s, k) => row([(k + 1) + '. ' + (s.name || s.code), s.arrive || s.eta, fmt(s.drive_min),
            s.wait_min >= 0.5 ? fmt(s.wait_min) + ' (մինչև ' + s.eta + ')' : '0', fmt(s.unload_min), windowText(s.window) || '—']));
        row(['Վերադարձ պահեստ', tr.return, fmt(x.back_min), '', '', '']);
        const wrap = document.createElement('div');
        wrap.className = 'dp-why-scroll';   // на телефоне таблица прокручивается в своей рамке, страница — нет
        wrap.appendChild(table);
        body.appendChild(wrap);
        fold.appendChild(body);
        return fold;
    }
    // Машина с несколькими рейсами: строка на рейс (минуты — в сумме «загрузка → возвращение») и итог дня машины
    function whyTruck(t) {
        const trips = t.trips.map((tr, k) => {
            const tt = tripTime(tr);
            return 'Երթ ' + (k + 1) + '՝ ' + tr.loading_start + ' → ' + tr.return + ' · ' + pl(tr.stops.length, 'կետ') + ' · ' + kgText(tr.kg)
                + (tr.load_pct !== null ? ' (' + tr.load_pct + '%)' : '') + ' · ≈ ' + fmt(tr.km, 1) + NB + 'կմ'
                + (tr.liters !== null ? ' · ≈ ' + fmt(tr.liters, 1) + NB + 'լ' : '') + ' · '
                + (tt.loading ? 'բեռնում ' + minText(tt.loading) + ', ' : '') + 'ճանապարհին ' + minText(tt.drive)
                + ', բեռնաթափում ' + minText(tt.unload) + (tt.wait ? ', ընդունման ժամի սպասում ' + minText(tt.wait) : '') + '։';
        });
        const last = t.trips[t.trips.length - 1].explain, end = state.data.work_end;
        const box = document.createElement('section');
        box.setAttribute('aria-label', 'Մեքենայի երթերը');
        box.append(whyTitle('Մեքենայի երթերը'), whyList([['Երթեր', trips], ['Ընդամենը', [
            pl(t.trips.length, 'երթ') + ', ' + pl(t.stops, 'կետ') + ', ' + kgText(t.kg) + ', ≈ ' + fmt(t.km, 1) + NB + 'կմ, ≈ ' + fmt(t.liters, 1) + NB + 'լ դիզել։',
            'Վերադարձ՝ ' + t.return + ', ' + (last.end_slack_min >= -0.5 ? 'մինչև աշխատանքային օրվա ավարտը (' + end + ') մնում է ' + minText(last.end_slack_min)
                : 'աշխատանքային օրվա ավարտից (' + end + ') ' + minText(-last.end_slack_min) + ' ուշ') + '։',
            'Մանրամասն բացատրության համար ընտրեք երթը վերևում։']]]));
        return box;
    }
    // «Բոլորը»: что учитывает расчёт дня — свёрнуто по умолчанию
    function whyDay(plan) {
        const e = plan.explain, m = e.model || {}, d = state.data, o = d.orders, sm = plan.summary;
        const wear = e.trucks.some(x => x.wear);
        const fuelOf = (x) => (x.fuel_empty_l100 !== null ? 'դատարկ՝ ' + fmt(x.fuel_empty_l100, 1) + ', լրիվ բեռնված՝ ' + fmt(x.fuel_full_l100, 1) : fmt(x.l100, 1)) + NB + 'լ/100' + NB + 'կմ';
        const speed = m.speed_city_source === m.speed_region_source
            ? 'քաղաքում ' + fmt(m.speed_city_kmh, 1) + NB + 'կմ/ժ, մարզում ' + fmt(m.speed_region_kmh, 1) + NB + 'կմ/ժ (' + (SPEED_SRC[m.speed_city_source] || '') + ')'
            : 'քաղաքում ' + fmt(m.speed_city_kmh, 1) + NB + 'կմ/ժ (' + (SPEED_SRC[m.speed_city_source] || '') + '), մարզում ' + fmt(m.speed_region_kmh, 1) + NB + 'կմ/ժ (' + (SPEED_SRC[m.speed_region_source] || '') + ')';
        const minutes = m.minutes === 'yandex' ? 'Ճանապարհի ժամանակը վերցվում է Յանդեքսի կանխատեսումից։'
            : m.minutes === 'valhalla' ? 'Ճանապարհի ժամանակը վերցվում է Valhalla-ից՝ բեռնատարի համար։'
            : m.minutes === 'zones' ? 'Ճանապարհի ժամանակը հաշվվում է արագությամբ՝ ' + speed + '։' : '';
        const learned = [];
        if (m.learned) {
            if (m.learned.travel) learned.push('ճանապարհին ծախսվող ժամանակն ըստ ժամերի');
            if (m.learned.unload) learned.push('բեռնաթափման ժամանակը');
            if (m.learned.loading) learned.push('բեռնման ժամանակը');
            if ((m.learned.fuel || []).length) learned.push('դիզելի ծախսը՝ ' + m.learned.fuel.map(c => truckLabel(truckBy(c))).join(', '));
        }
        // загрузка на складе — те же числа, что прибавляет расчёт (tn.load), даже если задано только одно из двух
        const loadingSet = num(e.loading_fixed_min) > 0 || num(e.loading_min_per_tonne) > 0;
        const cost = 'օրվա դիզելի' + (wear ? ' (և մաշվածքի, որտեղ նշված է)' : '') + ' ծախս';   // + «ը», перед гласной — «ն»
        const rows = [
            ['Պատվերներ', ['Առաքման համար՝ ' + pl(o.count, 'պատվեր') + ', ' + pl(o.customers, 'խանութ') + ', ' + kgText(o.kg) + '։ Երթերում՝ '
                    + pl(sm.stops, 'խանութ') + ', ' + kgText(sm.kg) + '։',
                e.carried ? 'Նախորդ օրից այստեղ է տեղափոխված ' + pl(e.carried, 'պատվեր') + '։' : '',
                e.deferred ? 'Այս օրից հաջորդ օր է տեղափոխված ' + pl(e.deferred, 'պատվեր') + '։' : '']],
            ['Մեքենաներ', e.trucks.map(x => truckLabel(x) + '՝ մինչև ' + kgText(x.capacity_kg) + ', ' + fuelOf(x) + (x.center_ok ? ', մտնում է կենտրոն' : '')
                + (x.trips ? '' : ', այսօր երթ չունի') + '։')],
            ['Բեռ', ['Երթի բեռը՝ մեքենայի տոննաժի առավելագույնը ' + e.load_cap_pct + '%-ը։ Ավելի ծանր կարող է լինել միայն մեկ պատվերով երթը, '
                    + 'եթե այդ պատվերն այլ կերպ չի տեղավորվում։',
                'Մեքենայից ծանր պատվերը բաժանվում է մի քանի երթի՝ հավասար։',
                e.balance_load_aware
                    ? 'Խանութն ավելի ծանր երթից տեղափոխվում է ավելի թեթև երթ, եթե դա նվազեցնում է դիզելի' + (wear ? ' և մաշվածքի' : '') + ' ծախսը՝ հաշվի առնելով բեռը, '
                        + 'իսկ կիլոմետրերն ու լիտրերն աճում են ոչ ավելի, քան ' + e.balance_slack_pct + '%-ով։'
                    : e.balance_from_pct + '%-ից ծանր երթերից խանութները տեղափոխվում են ավելի թեթև երթեր, եթե կիլոմետրերն ու լիտրերն աճում են ոչ ավելի, քան '
                        + e.balance_slack_pct + '%-ով։']],
            ['Աշխատանքային օր', ['Մեքենաներն աշխատում են ' + d.work_start + '–' + d.work_end + '։ ' + (d.overtime_ok
                ? 'Սեղմված է «Տանել ' + d.work_end + '-ից հետո»՝ թույլատրվում է մինչև ' + d.overtime_end + '-ը։'
                : 'Ավելի ուշ՝ միայն «Տանել ' + d.work_end + '-ից հետո» կոճակով, մինչև ' + d.overtime_end + '-ը։')]],
            ['Կենտրոն', [e.zone ? (e.center_stores ? pl(e.center_stores, 'խանութ') + ' փոքր կենտրոնում է՝ դրանք տանում են միայն կենտրոն մտնող մեքենաները։'
                    : 'Այս օրը փոքր կենտրոնում խանութ չկա։') : 'Փոքր կենտրոնի սահմանը կարգավորումներում նշված չէ։',
                e.zone && m.bypass ? 'Կենտրոնից դուրս կետերի միջև ճանապարհը հաշվվում է կենտրոնը շրջանցելով։'
                    : e.zone && m.bypass === false ? 'Կենտրոնի շրջանցումը չի հաշվվում՝ ' + noBypass(m) + '։' : '']],
            ['Ընդունման ժամեր', [e.window_stores ? pl(e.window_stores, 'խանութ') + ' ունի ընդունման ժամ՝ երթերը կազմվում են այնպես, որ հասնենք ժամանակին, իսկ վաղ հասնելու դեպքում մեքենան սպասում է։'
                : 'Այս օրվա խանութներից ոչ մեկն ընդունման ժամ չունի։']],
            ['Մեքենաների սահմանափակումներ', [e.access_stores ? pl(e.access_stores, 'խանութ') + ' ունի մեքենաների սահմանափակում՝ դրանք տանում են միայն թույլատրված մեքենաները։' : '']],
            ['Ցածր արժեքով երթեր', [num(d.min_trip_revenue) > 0 ? 'Երթը, որի ապրանքի արժեքը ' + fmt(d.min_trip_revenue) + NB + 'դրամից պակաս է, նշվում է՝ այն կարելի է տանել հաջորդ օրը։' : '']],
            ['Ճանապարհներ', [kmSource(m) ? capFirst(kmSource(m)) + '։' : '',
                m.unsnapped ? pl(m.unsnapped, 'խանութ') + ' ճանապարհից ' + fmt(m.snap_km, 1) + NB + 'կմ-ից հեռու է՝ դրանց հեռավորությունը հաշվվում է ուղիղ գծով × ' + fmt(m.detour, 2) + '։' : '']],
            ['Ժամանակ', [minutes, m.hourly_gps ? 'Արագությունն ըստ ժամերի՝ մենեջերների GPS պատմությունից։' : '',
                'Բեռնաթափում՝ ' + fmt(e.unload_min_per_stop, 1) + NB + 'րոպե յուրաքանչյուր խանութում + ' + fmt(e.unload_min_per_tonne, 1) + NB + 'րոպե յուրաքանչյուր տոննայի համար'
                    + (e.unload_stores ? '՝ ' + pl(e.unload_stores, 'խանութի') + ' համար՝ իր ճշգրտումով' : '') + '։',
                loadingSet ? 'Բեռնում պահեստում՝ ' + fmt(e.loading_fixed_min, 1) + NB + 'րոպե + ' + fmt(e.loading_min_per_tonne, 1) + NB + 'րոպե յուրաքանչյուր տոննայի համար։'
                    : 'Պահեստում բեռնման ժամանակը նշված չէ կամ 0 է՝ հաշվի չի առնվում։']],
            ['Վարորդների տվյալներ', [learned.length ? 'Վարորդների փաստացի տվյալներից սովորած և կիրառված՝ ' + learned.join('; ') + '։'
                : 'Վարորդների տվյալներից սովորած ճշգրտումներ դեռ չեն կիրառվում։']],
            ['Հաշվարկ', [e.solver ? 'Երթերը կազմում է ծրագիրը՝ սկզբում իր հաշվարկով, հետո PyVRP լուծիչով (մինչև ' + fmt(e.solver_iterations)
                    + ' քայլ․ լուծիչը դիզելը հաշվում է մեքենայի միջին ծախսով՝ լ/100 կմ)։ Լուծիչի արդյունքը վերցվում է, միայն եթե բոլոր կանոնները '
                    + 'պահպանված են, և եթե երթերում ավելի շատ պատվեր կա, կամ նույն պատվերներով ' + cost + 'ն ավելի չէ, կամ ' + e.load_cap_pct
                    + '%-ից ծանր երթերն ավելի քիչ են։'
                    : 'Երթերը կազմում է ծրագիրն իր հաշվարկով (PyVRP լուծիչը տեղադրված չէ)։',
                e.solver ? 'Նպատակը՝ նախ տանել հնարավորինս շատ պատվեր, ապա նվազագույնի հասցնել ' + cost + 'ը։'
                    : 'Նպատակը՝ կարճ ճանապարհ և քիչ դիզել։',
                e.pinned_trips ? 'Ամրացված երթեր՝ ' + e.pinned_trips + '․ վերակազմելիս դրանք չեն փոխվում։' : '']],
            ['Արդյունք', [pl(sm.trips, 'երթ') + ', ≈ ' + fmt(sm.km) + NB + 'կմ, ≈ ' + fmt(sm.liters) + NB + 'լ դիզել։']],
            ['Տվյալների որակ', ['Խանութի կետը վերցվում է այս հերթականությամբ՝ ձեռքով → վարորդի GPS → ERP հասցե → մենեջերի GPS։ '
                    + 'Եթե ERP հասցեի կետը մենեջերի GPS-ից ավելի քան ' + fmt(e.erp_gps_gap_km, 1) + NB + 'կմ հեռու է, վերցվում է մենեջերի GPS-ը։',
                o.no_coords ? pl(o.no_coords, 'խանութ') + ' առանց կետի է՝ երթերում չկա։' : '']],
        ];
        const fold = whyFold('day', 'Ի՞նչ է հաշվի առնված հաշվարկում', false);
        const body = document.createElement('div');
        body.className = 'dp-why-body';
        body.appendChild(whyList(rows));
        fold.appendChild(body);
        return fold;
    }

    function drawMap() {
        ensureMap();
        if (!state.map) return;
        const d = state.data, plan = d.plan;
        state.layers.clearLayers();
        state.map.invalidateSize();
        const bounds = [];
        const depot = d.depot ? [d.depot.lat, d.depot.lon] : null;
        // выбранная машина или рейс исчезли после пересборки — снова все
        const focus = state.mapFocus;
        const fTruck = focus && plan.trucks.find(t => t.car_code === focus.truck);
        if (focus && (!fTruck || (focus.trip != null && !fTruck.trips.some(tr => tr.id === focus.trip)))) state.mapFocus = null;
        const shown = (t, tr) => !state.mapFocus || (state.mapFocus.truck === t.car_code
            && (state.mapFocus.trip == null || state.mapFocus.trip === tr.id));
        mapFilter(plan);
        // фильтр и «почему так» над картой меняют её высоту (в колонке справа) — вписывать точки по новому размеру
        state.map.invalidateSize({ pan: false });
        const routes = [];   // [линия на карте, точки рейса по порядку]
        plan.trucks.forEach(t => {
            const color = truckColor(t.car_code);
            t.trips.forEach((tr, ti) => {
                if (!shown(t, tr)) return;
                const pts = tr.stops.filter(s => s.lat !== null).map(s => [s.lat, s.lon]);
                const line = depot ? [depot, ...pts, depot] : pts;
                if (line.length > 1) routes.push([L.polyline(line, { color, weight: state.mapFocus ? 4 : 3, opacity: .85, dashArray: ti % 2 ? '6 6' : null }).addTo(state.layers), line]);
                tr.stops.forEach((s, si) => {
                    if (s.lat === null) return;
                    bounds.push([s.lat, s.lon]);
                    // одна машина или рейс — точки с номерами по порядку объезда (у машины с несколькими рейсами — «рейс.точка»)
                    const label = state.mapFocus ? (state.mapFocus.trip == null && t.trips.length > 1 ? (ti + 1) + '.' : '') + (si + 1) : null;
                    const m = label === null
                        ? L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: color, fillOpacity: 1 })
                        : L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'dp-npin', html: '<span' + (String(label).length > 3 ? ' class="is-long"' : '') + ' style="background:' + color + '">' + label + '</span>', iconSize: [26, 26] }), keyboard: false });
                    m.bindTooltip(esc(truckLabel(t)) + ' · երթ ' + (ti + 1) + ' · №' + (si + 1) + '<br>' + esc(s.name || s.code));
                    const when = (s.eta ? 'ժամանում ≈ ' + s.eta : '') + (windowText(s.window) ? ' · ընդունման ժամ՝ ' + windowText(s.window) : '');
                    m.bindPopup('<div class="rt-pop"><b>' + esc(s.name || s.code) + '</b><br>' + esc(s.address || '') + '<br>'
                        + esc(kgText(s.kg)) + ' · ' + esc(money(s.revenue)) + '<br>' + esc(truckLabel(t)) + ', երթ ' + (ti + 1) + ', կետ ' + (si + 1)
                        + (when ? '<br>' + esc(when) : '') + '</div>');
                    m.addTo(state.layers);
                });
            });
        });
        plan.unassigned.forEach(s => {
            if (s.lat === null || state.mapFocus) return;
            bounds.push([s.lat, s.lon]);
            const why = s.no_room ? 'Չտեղավորվեց մինչև ' + state.data.work_end + '-ը՝ ' : s.no_window ? 'Չենք հասցնում ընդունման ժամին՝ '
                : s.no_vehicle ? 'Խանութը սպասարկող մեքենա չկա՝ ' : s.no_center ? 'Կենտրոն, այսօր չկա թույլատրված մեքենա՝ ' : 'Դեռ երթում չէ՝ ';
            L.circleMarker([s.lat, s.lon], { radius: 7, color: s.no_room || s.no_window || s.no_center || s.no_vehicle ? '#ff6b79' : '#ffb547', weight: 3, fillColor: '#0c0f14', fillOpacity: 1 })
                .bindTooltip(esc(why) + esc(s.name || s.code)).addTo(state.layers);
        });
        if (depot) {
            bounds.push(depot);
            L.marker(depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<i class="fas fa-warehouse"></i>', iconSize: [28, 28] }), keyboard: false })
                .bindTooltip('Պահեստ').addTo(state.layers);
        }
        state.mapBounds = bounds.length ? bounds : null;
        state.mapUserMoved = false;
        syncFocus();
        if (bounds.length) fitMap(state.map, bounds);
        roadLines(routes);
    }

    // Рейсы сначала рисуются по прямой, затем линии заменяются ответом сервера — по дорогам (участки между точками вне
    // малого центра — в объезд него, как считаются км рейсов). Нет связи или карты дорог на сервере — остаются прямые.
    // Ответ устарел (карту перерисовали) — не применяется.
    async function roadLines(routes) {
        const gen = ++state.roadGen;
        const key = line => line.map(p => p[0].toFixed(5) + ',' + p[1].toFixed(5)).join(';');
        const missing = routes.filter(([, line]) => !state.roadCache.has(key(line)));
        if (missing.length) {
            let data;
            try { data = await api('POST', '/api/routes/road-lines', { lines: missing.map(([, line]) => line), avoid_center: true }); } catch (e) { return; }
            if (!Array.isArray(data.lines)) return;
            missing.forEach(([, line], i) => state.roadCache.set(key(line), data.lines[i]));
        }
        if (gen !== state.roadGen) return;
        const all = [...(state.mapBounds || [])];
        routes.forEach(([pl, line]) => {
            const road = state.roadCache.get(key(line));
            if (Array.isArray(road) && road.length > 1) { pl.setLatLngs(road); all.push(...road); }
        });
        // дорога уходит дальше магазинов — вписать её целиком, если логист ещё не двигал карту
        state.mapBounds = all.length ? all : state.mapBounds;
        if (state.map && all.length && !state.mapUserMoved) fitMap(state.map, all);
    }

    // ---------- Действия ----------
    function setBusy(on) {
        state.busy = on;
        document.querySelectorAll('#dpBody button, #dpBody select').forEach(x => {
            if (on) { x.dataset.wasDisabled = x.disabled ? '1' : ''; x.disabled = true; } else if (x.dataset.wasDisabled !== undefined) { x.disabled = x.dataset.wasDisabled === '1'; delete x.dataset.wasDisabled; }
        });
        $('rtDispatch').setAttribute('aria-busy', String(on));
    }

    async function build() {
        if (state.busy) return;
        const trucks = selectedTrucks();
        if (!trucks.length) { showActionError(new Error('Նշեք գոնե մեկ մեքենա 1-ին քայլում։')); return; }
        hideActionError();
        setBusy(true);
        $('dpBuildText').textContent = 'Կազմում եմ երթերը…';
        try {
            const data = await api('POST', '/api/routes/dispatch/build', { date: state.day, trucks });
            state.geoChanged = null;
            setBusy(false);
            setData(data);
            toast('Երթերը կազմված են՝ ' + pl(data.plan.summary.trips, 'երթ') + ', ≈ ' + fmt(data.plan.summary.km) + NB + 'կմ');
            $('dpStep3Title').focus();
        } catch (e) {
            setBusy(false);
            render();
            showActionError(e);
        }
    }

    async function edit(body, okText) {
        if (state.busy) return null;
        hideActionError();
        setBusy(true);
        try {
            const data = await api('POST', '/api/routes/dispatch/edit', { date: state.day, rev: state.data.rev, ...body });
            setBusy(false);
            setData(data);
            if (okText) toast(okText + (data.delta_km !== undefined && data.delta_km !== null ? ' — ' + deltaText(data.delta_km) : '') + '։');
            return data;
        } catch (e) {
            setBusy(false);
            render();
            showActionError(e);
            return null;
        }
    }

    // Конец дня машин сегодня: принятая переработка («Везти после конца дня») — её предел
    const endOfDay = () => (state.data.overtime_ok ? state.data.overtime_end : state.data.work_end);

    // «Везти после конца дня»: не поместившиеся — по машинам дня с переработкой (форс-мажор)
    async function overtime() {
        if (state.busy) return;
        if (!window.confirm('Մեքենաները կաշխատեն ' + state.data.work_end + '-ից հետո՝ մինչև ' + state.data.overtime_end + '-ը։ Շարունակե՞լ։')) return;
        hideActionError();
        setBusy(true);
        try {
            const data = await api('POST', '/api/routes/dispatch/overtime', { date: state.day, rev: state.data.rev });
            setBusy(false);
            setData(data);
            const un = data.plan.unassigned;
            const late = un.filter(s => s.no_room).length, win = un.filter(s => s.no_window).length;
            const cen = un.filter(s => s.no_center).length;
            const rest = un.length - late - win - cen;
            const parts = [];
            if (late) parts.push(pl(late, 'խանութ') + ' չեն հասցնում նույնիսկ մինչև ' + data.overtime_end + '-ը։');
            if (win) parts.push(pl(win, 'խանութ') + ' չենք հասցնում իրենց ընդունման ժամին։');
            if (cen) parts.push(pl(cen, 'խանութ') + ' կենտրոնում է, իսկ այսօր չկա կենտրոն մտնող մեքենա։');
            if (rest) parts.push(pl(rest, 'խանութ') + ' դեռ ոչ մի երթում չեն։');
            toast(parts.length ? parts.join(' ') : 'Բոլոր խանութները երթերում են' + (data.overtime ? '՝ արտաժամյա' : '') + '։');
        } catch (e) {
            setBusy(false);
            render();
            showActionError(e);
        }
    }

    async function excludeStop(stop) {
        if (!needPlan()) return;
        const orders = stop.orders || [];
        let ok = true;
        for (let i = 0; i < orders.length && ok; i++) {
            ok = !!(await edit({ action: 'exclude', order: orders[i].isn }, i === orders.length - 1 ? '«' + (stop.name || stop.code) + '» այսօր չենք տանում' : null));
        }
    }

    async function reset() {
        if (state.busy) return;
        if (!window.confirm('Ջնջե՞լ այս օրվա երթերը փոփոխությունների հետ միասին (տեղափոխումներ, ամրացումներ, «չենք տանում»)։')) return;
        setBusy(true);
        try {
            const data = await api('POST', '/api/routes/dispatch/reset', { date: state.day });
            state.editing.clear();
            setBusy(false);
            setData(data);
            toast('Օրվա պլանը ջնջված է — կարելի է նորից կազմել երթերը։');
        } catch (e) { setBusy(false); render(); showActionError(e); }
    }

    // ---------- Печать и Excel ----------
    function printSheets() {
        const d = state.data, plan = d.plan;
        if (!plan) return;
        const dayText = (WD_NAME[d.weekday] || '') + ', ' + dateRu(d.day);
        let html = '<!doctype html><html lang="hy"><head><meta charset="utf-8"><title>Առաքում ' + esc(dateRu(d.day)) + '</title><style>'
            + 'body{font-family:"Segoe UI",Sylfaen,"Noto Sans Armenian",Arial,sans-serif;color:#000;margin:0;padding:12mm;font-size:15px}'
            + '.sheet{page-break-after:always;break-after:page}.sheet:last-child{page-break-after:auto;break-after:auto}'
            + 'h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:18px 0 6px}.sub{font-size:15px;margin:0 0 10px}'
            + 'table{width:100%;border-collapse:collapse}th,td{border:1px solid #000;padding:7px 8px;vertical-align:top;text-align:left}'
            + 'th{font-size:13px;background:#eee}td.n{font-size:22px;font-weight:700;width:38px;text-align:center}td.kg{font-size:18px;font-weight:700;white-space:nowrap;width:90px}'
            + 'td.ok{width:60px}td.t{font-size:17px;font-weight:700;white-space:nowrap;width:110px}.win{font-size:13px;font-weight:400}'
            + '.addr{font-size:17px}.nm{font-weight:700}@media screen{body{background:#fff}}'
            + '</style></head><body>';
        plan.trucks.forEach(t => {
            html += '<section class="sheet"><h1>' + esc(truckLabel(t)) + '</h1><p class="sub">Առաքում՝ ' + esc(dayText) + ' · '
                + esc(pl(t.trips.length, 'երթ')) + ' · ' + esc(pl(t.stops, 'կետ')) + ' · ' + esc(kgText(t.kg))
                + ' · ≈ ' + esc(fmt(t.km)) + ' կմ</p>';
            t.trips.forEach((tr, i) => {
                html += '<h2>Երթ ' + (i + 1) + '՝ մեկնում ' + esc(tr.depart) + ', ' + esc(kgText(tr.kg)) + ', ≈ ' + esc(fmt(tr.km)) + ' կմ</h2>'
                    + '<table><thead><tr><th>№</th><th>Խանութ և հասցե</th><th>Ժամանում</th><th>Բեռ</th><th>Նշում</th></tr></thead><tbody>';
                tr.stops.forEach((s, si) => {
                    const win = windowText(s.window);
                    html += '<tr><td class="n">' + (si + 1) + '</td><td><div class="nm">' + esc(s.name || s.code) + ' <small>(' + esc(s.code) + ')</small></div>'
                        + '<div class="addr">' + esc(s.address || 'ERP-ում հասցե չկա') + '</div></td>'
                        + '<td class="t">' + esc(s.eta ? '≈ ' + s.eta : '') + (win ? '<div class="win">ընդունում է՝ ' + esc(win) + '</div>' : '')
                        + (s.center ? '<div class="win">Կենտրոն</div>' : '') + '</td>'
                        + '<td class="kg">' + esc(kgText(s.kg)) + '</td><td class="ok"></td></tr>';
                });
                html += '</tbody></table>';
            });
            html += '</section>';
        });
        html += '</body></html>';
        const w = window.open('', '_blank');
        if (!w) { showActionError(new Error('Զննարկիչը թույլ չտվեց բացել տպման պատուհանը — թույլատրեք թռուցիկ պատուհանները այս կայքի համար։')); return; }
        w.document.open();
        w.document.write(html);
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* окно закрыли раньше */ } }, 300);
    }

    function exportExcel() {
        const d = state.data, plan = d.plan;
        if (!plan) return;
        if (typeof window.XLSX === 'undefined') { showActionError(new Error('Excel-ի գրադարանը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։')); return; }
        const rows = [['Մեքենա', 'Երթ', 'Մեկնում', 'Վերադարձ', '№', 'Ժամանում', 'Ընդունման ժամ', 'Կենտրոն', 'Կոդ', 'Խանութ', 'Հասցե', 'Մենեջեր',
            'Բեռ, կգ', 'Գումար, դրամ', 'Լայնություն', 'Երկայնություն']];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => tr.stops.forEach((s, si) => rows.push([
            truckLabel(t), i + 1, tr.depart, tr.return, si + 1, s.eta || '', windowText(s.window), s.center ? 'այո' : '',
            s.code, s.name, s.address, s.agent_name || s.agent_code, s.kg, Math.round(s.revenue / (s.share || 1)), s.lat, s.lon]))));
        const sum = [['Մեքենա', 'Երթեր', 'Կետեր', 'Բեռ, կգ', 'կմ', 'Լիտր', 'Մաշվածք, դրամ', 'Դիզել և մաշվածք, դրամ', 'Վերադարձ', 'Նորմերը լրացված են']];
        plan.trucks.forEach(t => sum.push([truckLabel(t), t.trips.length, t.stops, t.kg, t.km, t.liters,
            t.wear_amd, t.operating_cost_amd, t.return, t.fuel_load_configured && t.wear_configured ? 'այո' : 'ոչ']));
        sum.push(['Ընդամենը', plan.summary.trips, plan.summary.stops, plan.summary.kg, plan.summary.km,
            plan.summary.liters, plan.summary.wear_amd, plan.summary.operating_cost_amd, '', '']);
        if (plan.baseline) sum.push(['Եթե ըստ մենեջերների', plan.baseline.trips, '', '', plan.baseline.km,
            plan.baseline.liters, plan.baseline.wear_amd, plan.baseline.operating_cost_amd, '', '']);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), 'Երթեր');
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(sum), 'Մեքենաներ');
        const pending = [['Կոդ', 'Խանութ', 'Հասցե', 'Բեռ, կգ', 'Պատճառ']];
        (d.stops_no_coords || []).forEach(s => pending.push([s.code, s.name, s.address, s.kg, 'Տեղը քարտեզում նշված չէ']));
        plan.unassigned.forEach(s => pending.push([s.code, s.name, s.address, s.kg,
            s.no_vehicle ? 'Խանութը սպասարկող մեքենա չկա' : s.no_window ? 'Ընդունման ժամին չենք հասնում' : s.no_center ? 'Կենտրոն մտնելու մեքենա չկա' : 'Սմենայում չի տեղավորվում']));
        if (pending.length > 1) XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(pending), 'Երթերում չկան');
        XLSX.writeFile(wb, 'araqum_' + d.day + '.xlsx');
        announce('Excel ֆայլը ներբեռնված է');
    }

    // ---------- План и факт ----------
    async function loadFact() {
        const out = $('dpFactOut');
        out.innerHTML = '<p class="dp-note"><span class="rt-spin-inline" aria-hidden="true"></span> Հաշվում եմ փաստը և պլանը ' + esc(dateRu(state.day)) + '-ի համար…</p>';
        $('dpFactBtn').disabled = true;
        try {
            const r = (await api('GET', '/api/routes/dispatch/fact?date=' + encodeURIComponent(state.day))).fact;
            out.textContent = '';
            if (!r.stops) { out.innerHTML = '<p class="dp-note">Այդ օրը լրացված տոննաժով մեքենաները ոչինչ չեն տարել։</p>'; return; }
            const f = r.fact, p = r.plan;
            const pct = f.km > 0 ? Math.round((f.km - p.km) / f.km * 100) : 0;
            const lead = document.createElement('p');
            lead.className = 'dp-fact-lead';
            lead.textContent = r.saved_km > 0
                ? 'Ըստ պլանի կլիներ ' + fmt(r.saved_km) + NB + 'կմ-ով պակաս (' + pct + '%) և ≈ ' + fmt(f.liters - p.liters) + NB + 'լ-ով պակաս դիզել։'
                : 'Փաստացի բաշխումը պլանից վատ չէր՝ ' + fmt(f.km) + ' ընդդեմ ' + fmt(p.km) + NB + 'կմ։';
            out.appendChild(lead);
            const t = document.createElement('table');
            t.className = 'rt-table dp-small';
            t.innerHTML = '<thead><tr><th scope="col"></th><th scope="col">կմ</th><th scope="col">լիտր</th><th scope="col">երթեր</th><th scope="col">մեկ օրում չեն հասցնում</th></tr></thead><tbody></tbody>';
            [['Ինչպես առաքեցին (ERP)', f], ['Ըստ ծրագրի պլանի', p]].forEach(([nm, x]) => {
                const tr = document.createElement('tr');
                [nm, fmt(x.km), fmt(x.liters), fmt(x.trips), fmt(x.trips_over_time)].forEach((v, i) => {
                    const td = document.createElement('td');
                    td.textContent = v;
                    td.setAttribute('data-label', ['', 'կմ', 'լիտր', 'երթեր', 'մեկ օրում չեն հասցնում'][i]);
                    if (i) td.className = 'num w-half'; else td.className = 'rt-cell-name';
                    tr.appendChild(td);
                });
                t.querySelector('tbody').appendChild(tr);
            });
            const wrap = document.createElement('div');
            wrap.className = 'rt-table-scroll';
            wrap.appendChild(t);
            out.appendChild(wrap);
            const note = document.createElement('p');
            note.className = 'dp-note';
            note.textContent = pl(r.stops, 'կետ') + ', ' + kgText(r.kg) + ', մեքենաներ՝ ' + r.trucks.map(c => truckLabel(truckBy(c))).join(', ') + '։'
                + (r.skipped.docs ? ' Համեմատության մեջ չեն՝ ' + pl(r.skipped.docs, 'փաստաթուղթ') + ' (' + kgText(r.skipped.kg) + ') — ERP-ում մեքենա չկա, կարգավորումներում մեքենան առանց տոննաժի է, կամ խանութը առանց կետի է։' : '');
            out.appendChild(note);
        } catch (e) {
            out.textContent = '';
            const p = document.createElement('p');
            p.className = 'rt-ferr';
            p.textContent = e.message;
            out.appendChild(p);
        } finally { $('dpFactBtn').disabled = false; }
    }

    // ---------- «Հարցրու AI-ին» (ответ владельца №52) ----------
    const AI_SUGGEST = [
        'Ամփոփիր օրը երեք նախադասությամբ',
        'Ո՞ր մեքենան ունի ամենաշատ ազատ տեղ',
        'Ո՞ր երթն է ամենաուշը վերադառնում, և ինչու',
        'Ինչու՞ են որոշ խանութներ դուրս մնացել երթերից',
        'Ի՞նչ կլինի, եթե այսօր մեկ մեքենա չաշխատի',
    ];
    const AI_HISTORY = 12;          // реплик истории в запросе — как ai_chat.MAX_HISTORY
    const aiChat = () => {
        if (!state.ai.chats.has(state.day)) state.ai.chats.set(state.day, []);
        return state.ai.chats.get(state.day);
    };
    // Что выбрано на карте — подсказка модели к вопросу («почему так?» о выбранном рейсе)
    function aiFocus() {
        const f = state.mapFocus, plan = state.data && state.data.plan;
        const t = f && plan && plan.trucks.find(x => x.car_code === f.truck);
        if (!t) return null;
        const i = f.trip == null ? -1 : t.trips.findIndex(tr => tr.id === f.trip);
        return truckLabel(t) + (i >= 0 ? ', երթ ' + (i + 1) : t.trips.length > 1 ? ', բոլոր երթերը' : ', երթ 1');
    }
    function aiOpen(open) {
        $('dpAi').hidden = !open;
        $('dpAiOpen').hidden = open;
        $('dpAiOpen').setAttribute('aria-expanded', String(open));
        if (open) { aiRender(); $('dpAiInput').focus(); } else $('dpAiOpen').focus();
    }
    // Ответ AI — абзацы и строки-пункты «• …» (без разметки: всё через textContent)
    function aiText(box, text) {
        let ul = null;
        String(text).replace(/\*\*(.+?)\*\*/g, '$1').split('\n').forEach(raw => {
            const line = raw.trim();
            if (!line) { ul = null; return; }
            const m = /^(?:[•\-*]|\d+[.)])\s+(.+)$/.exec(line);
            if (m) {
                if (!ul) { ul = document.createElement('ul'); box.appendChild(ul); }
                const li = document.createElement('li');
                li.textContent = m[1];
                ul.appendChild(li);
            } else {
                ul = null;
                const p = document.createElement('p');
                p.textContent = line;
                box.appendChild(p);
            }
        });
    }
    function aiMsg(role, text, note) {
        const div = document.createElement('div');
        div.className = 'dp-ai-msg ' + (role === 'user' ? 'is-user' : 'is-bot');
        if (role === 'user') div.textContent = text; else aiText(div, text);
        if (note) { const n = document.createElement('span'); n.className = 'dp-ai-note'; n.textContent = note; div.appendChild(n); }
        return div;
    }
    function aiRender() {
        const log = $('dpAiLog'), chat = aiChat();
        log.textContent = '';
        state.ai.shownDay = state.day;
        $('dpAiSub').textContent = 'Պատասխանում է ' + dayHuman(state.day) + ' թվերով · ոչինչ չի փոխում';
        if (!chat.length) {
            const hello = document.createElement('p');
            hello.className = 'dp-ai-hello';
            hello.textContent = 'Հարցրեք այս օրվա երթերի, մեքենաների, խանութների կամ ժամերի մասին։ '
                + 'AI-ն տեսնում է նույն թվերը, ինչ էջը, և ոչինչ չի փոխում։ Օրինակ՝';
            const sugs = document.createElement('div');
            sugs.className = 'dp-ai-sugs';
            AI_SUGGEST.forEach(q => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'dp-ai-sug';
                b.innerHTML = '<i class="fas fa-arrow-right" aria-hidden="true"></i><span></span>';
                b.lastChild.textContent = q;
                b.addEventListener('click', () => aiAsk(q));
                sugs.appendChild(b);
            });
            log.append(hello, sugs);
        }
        chat.forEach(m => log.appendChild(aiMsg(m.role, m.text, m.note)));
        log.scrollTop = log.scrollHeight;
    }
    // fromInput — вопрос из поля ввода (его очистить); подсказка — набранный текст не трогать
    async function aiAsk(question, fromInput) {
        const q = String(question || '').trim();
        if (!q || state.ai.busy || !state.day) return;
        const day = state.day, chat = aiChat(), log = $('dpAiLog'), focus = aiFocus();
        // история — только удачные пары вопрос/ответ (чередование user/assistant для API)
        const history = chat.slice(-AI_HISTORY).map(m => ({ role: m.role, text: m.text }));
        if (!chat.length) log.textContent = '';
        const asked = [aiMsg('user', q)];
        if (focus) { const f = document.createElement('span'); f.className = 'dp-ai-focus'; f.textContent = 'քարտեզում՝ ' + focus; asked.push(f); }
        log.append(...asked);
        const wait = document.createElement('div');
        wait.className = 'dp-ai-msg is-bot';
        wait.innerHTML = '<span class="dp-ai-typing"><span class="dp-ai-dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Նայում եմ օրվա թվերին…</span></span>';
        log.appendChild(wait);
        log.scrollTop = log.scrollHeight;
        state.ai.busy = true;
        $('dpAiSend').disabled = true;
        if (fromInput) { $('dpAiInput').value = ''; aiResize(); }
        $('dpAiInput').focus();         // нажатая подсказка или «Կրկնել» исчезли — фокус в поле ввода
        try {
            const r = await api('POST', '/api/routes/dispatch/ask', { date: day, question: q, history, focus }, 150000);
            const note = r.truncated ? 'Պատասխանը կտրվել է՝ շատ երկար էր։ Հարցրեք ավելի նեղ։' : '';
            chat.push({ role: 'user', text: q }, { role: 'assistant', text: r.answer, note });
            if (wait.isConnected) wait.replaceWith(aiMsg('assistant', r.answer, note));
            else if (state.day === day && !$('dpAi').hidden) aiRender();     // панель перерисовали, пока ждали
            announce('AI-ն պատասխանեց');
        } catch (e) {
            if (state.day !== day) return;
            const err = document.createElement('div');
            err.className = 'dp-ai-msg is-err';
            err.setAttribute('role', 'alert');
            err.textContent = e.message || String(e);
            const again = document.createElement('button');
            again.type = 'button';
            again.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            again.innerHTML = '<i class="fas fa-rotate-right" aria-hidden="true"></i><span>Կրկնել</span>';
            again.addEventListener('click', () => {
                if (state.ai.busy) return;      // идёт другой вопрос — не терять этот
                err.remove(); asked.forEach(x => x.remove()); aiAsk(q);
            });
            err.appendChild(document.createElement('br'));
            err.appendChild(again);
            if (wait.isConnected) wait.replaceWith(err); else { aiRender(); log.appendChild(err); }
        } finally {
            state.ai.busy = false;
            $('dpAiSend').disabled = false;
            log.scrollTop = log.scrollHeight;
        }
    }
    function aiResize() {
        const el = $('dpAiInput');
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight + 2, 150) + 'px';
    }
    function aiInit() {
        if (!$('dpAi')) return;
        $('dpAiOpen').addEventListener('click', () => aiOpen(true));
        $('dpAiClose').addEventListener('click', () => aiOpen(false));
        $('dpAiNew').addEventListener('click', () => { if (state.ai.busy) return; state.ai.chats.set(state.day, []); aiRender(); $('dpAiInput').focus(); });
        $('dpAiForm').addEventListener('submit', (e) => { e.preventDefault(); aiAsk($('dpAiInput').value, true); });
        $('dpAiInput').addEventListener('input', aiResize);
        // Enter — отправить, Shift+Enter — новая строка; Esc — закрыть панель
        $('dpAiInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); aiAsk($('dpAiInput').value, true); }
        });
        $('dpAi').addEventListener('keydown', (e) => { if (e.key === 'Escape') aiOpen(false); });
    }

    // ---------- Старт ----------
    function init() {
        const params = new URLSearchParams(window.location.search);
        const day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('date') || '') ? params.get('date') : null;
        $('dpDate').addEventListener('change', () => { if (/^\d{4}-\d{2}-\d{2}$/.test($('dpDate').value)) goDay($('dpDate').value); });
        $('dpTomorrow').addEventListener('click', () => goDay(state.data ? state.data.default_day : null));
        $('dpDayPrev').addEventListener('click', () => { if (state.day) goDay(shiftDay(state.day, -1)); });
        $('dpDayNext').addEventListener('click', () => { if (state.day) goDay(shiftDay(state.day, 1)); });
        $('dpRefresh').addEventListener('click', () => load(state.day, true));
        $('dpRetry').addEventListener('click', () => load(state.day || day));
        $('dpBuild').addEventListener('click', build);
        $('dpReset').addEventListener('click', reset);
        $('dpPrint').addEventListener('click', printSheets);
        $('dpExcel').addEventListener('click', exportExcel);
        $('dpFactBtn').addEventListener('click', loadFact);
        $('dpActionReload').addEventListener('click', () => { hideActionError(); load(state.day); });
        $('dpPickSave').addEventListener('click', savePick);
        $('dpPickCoord').addEventListener('input', () => {
            const p = parseCoord($('dpPickCoord').value);
            $('dpPickSave').disabled = !p;
            if (p) setPick(p[0], p[1]);
        });
        $('dpNoCoords').addEventListener('toggle', () => { if ($('dpNoCoords').open) ensurePickMap(); });
        $('dpGeoSug').addEventListener('toggle', () => { if ($('dpGeoSug').open) renderGeoSug(); });
        $('dpGeoSave').addEventListener('click', () => saveGeo(false));
        $('dpGeoAuto').addEventListener('click', () => saveGeo(true));
        $('dpGeoCancel').addEventListener('click', () => $('dpGeoDlg').close());
        $('dpGeoDlg').addEventListener('close', () => { state.geoStop = null; });
        $('dpGeoCoord').addEventListener('input', () => {
            const p = parseCoord($('dpGeoCoord').value);
            $('dpGeoSave').disabled = !p;
            if (p) setGeo(p[0], p[1], false);
        });
        $('dpUnloadSave').addEventListener('click', () => saveUnload(false));
        $('dpUnloadClear').addEventListener('click', () => saveUnload(true));
        $('dpUnloadCancel').addEventListener('click', () => $('dpUnloadDlg').close());
        $('dpUnloadDlg').addEventListener('close', () => { state.unloadStop = null; state.unloadInfo = null; });
        $('dpUnloadDlg').addEventListener('cancel', (e) => { if (state.busy) e.preventDefault(); });
        $('dpUnloadMin').addEventListener('input', () => { $('dpUnloadErr').textContent = ''; markUnload(false); });
        $('dpUnloadMin').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveUnload(false); } });
        $('dpMapBox').addEventListener('toggle', () => { if ($('dpMapBox').open && state.data && state.data.plan) drawMap(); });
        $('dpStep1Tog').addEventListener('click', () => toggleStep('dpStep1'));
        $('dpStep2Tog').addEventListener('click', () => toggleStep('dpStep2'));
        // высота закреплённой шапки дашборда — карта закрепляется под ней (меню на узком экране раскрывается — пересчитать)
        const nav = document.querySelector('.navbar');
        const navH = () => $('rtDispatch').style.setProperty('--rt-nav-h',
            (nav && getComputedStyle(nav).position === 'sticky' ? Math.ceil(nav.getBoundingClientRect().height) : 0) + 'px');
        navH();
        if (nav && typeof window.ResizeObserver !== 'undefined') new ResizeObserver(navH).observe(nav); else window.addEventListener('resize', navH);
        aiInit();
        setInterval(poll, 30 * 1000);       // poll() сам проверяет, прошло ли 5 минут (и после сна компьютера тоже)
        document.addEventListener('visibilitychange', poll);
        load(day);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

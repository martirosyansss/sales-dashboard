/* «Развоз» /routes/dispatch — план развоза на завтра (docs/plans/dispatch-plan.md).
   Данные: GET /api/routes/dispatch?date=…; «Собрать рейсы» — POST /api/routes/dispatch/build;
   правки логиста — POST /api/routes/dispatch/edit (move | pin | unpin | exclude | include | resize — конец
   полосы рейса тянут мышью на шкале дня, пока тянут — resize с preview, undo — «Չեղարկել», с номером черновика rev); «Начать заново» — POST /api/routes/dispatch/reset; ручная точка магазина —
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
   «Հարցրու AI-ին» (ответ владельца №52) — панель чата в routes_dispatch_ai.js (подключается в init): вопрос логиста
   по дню, сервер отвечает по тем же цифрам дня и ничего не меняет; переводы её ошибок — здесь, в SERVER_HY.
   «Բեռնագիր» у каждой машины (ответ владельца №57) — GET /api/routes/dispatch/waybill?date=…&truck=…&rev=…: товары рейсов
   машины на загрузку; печать и Excel только этой машины. Водитель машины (№62) — кнопка «Վարորդ» у карточки,
   POST /api/routes/dispatch/driver: с выбранного дня до следующей смены; имя — в шапке карточки и в накладной.
   Новые заказы дня (ответ владельца №72): заказ, принятый сегодня, — развоз завтра; на странице сегодняшнего дня плашка
   «Այսօր եկել է N նոր պատվեր» (same_day в ответе дня и в /status — без перезагрузки) и диалог: варианты «как везти сегодня»
   (POST /api/routes/dispatch/same-day), выбор — правка same_day, «Թողնել վաղվան».
   Утверждение плана дня (ответ владельца №73): «Հաստատել օրվա պլանը» — правка approve (все рейсы закреплены), отметка
   «Պլանը հաստատված է · ժ. ЧЧ:ММ · кто»; пока утверждён, «Վերակազմել երթերը» и «Ջնջել երթերը» недоступны (сервер — 400),
   «Չեղարկել հաստատումը» — правка unapprove.
   Отправка водителям (ответ владельца №81, как «Publish changes» у Routific): утверждение отправляет план на терминалы;
   правки после него копятся — у даты метка «N մեքենայի փոփոխությունը չի ուղարկվել», кнопка «Ուղարկել» (правка send) и
   «Չեղարկել փոփոխությունները» (правка discard — назад к отправленному); бумага при неотправленных правках — после
   «Ուղարկել և շարունակել» (dpSendFirstDlg), чтобы лист и «Բեռնագիր» совпадали с терминалом водителя.
   Как у профессиональных систем (№81): всё, что требует внимания, — строкой счётчиков под «Ի՞նչ անել հիմա» (dpInbox);
   рейсы и карта — рабочий экран на высоту окна со своей прокруткой; магазин можно перетащить мышью на полосу рейса
   шкалы или в рейс карточки (правка move); на телефоне — вкладки внизу «Օր · Երթեր · Քարտեզ» (dpTabs).
   Вариант А (ответ владельца №82, как Routific / Яндекс): на широком экране с рейсами — рабочий экран на высоту окна
   (layoutWs): карта с итогами поверх, справа — карточка выбранной машины, снизу — машины и шкала дня (кружки с номерами
   магазинов, загрузка %, ⚠); шаги 1–2 и пересборка — в выдвижной панели «Մեքենաներ, պատվերներ». Узлы страницы не
   пересоздаются — переносятся в слоты и обратно (id и обработчики те же).
   Ход дня (№82, как мониторинг Яндекса): сегодня кружки магазинов на шкале — по факту «Առաքիչ» (GET
   /api/routes/dispatch/progress раз в минуту): доставлен, частично, отказ, машина на месте, опаздывает; у машины «✓ 7/17». */
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
        pickMap: null, pickMarker: null, pickCid: null, dragging: false, undo: null,
        loadSeq: 0,                         // номер последнего запроса дня: ответы на прежние запросы не применяются
        editing: new Set(),                 // рейсы, открытые кнопкой «Փոփոխել»
        tab: 'day',                         // №81: вкладка на телефоне — day | trips | map
        dragStop: null,                     // №81: магазин, который тянут мышью на другой рейс: {stop, from}
        boardFolded: false,                 // №81: шкала дня свёрнута (как «скрыть таймлайн» у Routific)
        fetchedAt: 0,                       // когда последний раз спрашивали сервер о заказах дня
        geoMap: null, geoMarker: null, geoStop: null,   // «Փոխել տեղը»: карта диалога и магазин
        sugMap: null, sugLayer: null, sugSel: null,     // предложения водителей: карта и выбранное (event_id)
        geoChanged: null,                   // день, в котором после сборки меняли точку магазина — подсказать пересборку
        unloadStop: null, unloadInfo: null, unloadSeq: 0,   // «Ժամանակ խանութում»: магазин диалога, его данные с сервера, номер запроса
        stepsOpen: new Set(),               // шаги 1–2, раскрытые логистом после сборки (иначе свёрнуты в строку)
        open: new Set(),                    // раскрытые карточки машин (код машины)
        driverCar: null,                    // «Վարորդ»: машина открытого диалога
        agentsPick: null,                   // «Մենեջերներ»: выбор, ещё не применённый к плану дня {day, off: Set agent_id}
        sd: null,                           // диалог новых заказов дня (№72): {pick: Set fISN, options, blocked, forIsns, busy}
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
    // накладная (բեռնագիր) разошлась бы с планом на экране: rev на сервере другой или состав рейсов машины не тот (fetchWaybill)
    const WB_STALE_HY = 'Էջը բացելուց հետո պլանը փոխվել է․ բեռնագիրը չէր համընկնի էկրանի պլանի հետ։ Թարմացրեք էջը։';
    const SERVER_HY = {
        'ожидался JSON-объект': BAD_REQUEST,
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
        'Список менеджеров не принят — обновите страницу': 'Մենեջերների ցուցակը չընդունվեց — թարմացրեք էջը',
        'ожидался список менеджеров': 'Մենեջերների ցուցակը չընդունվեց — թարմացրեք էջը',
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
        // план на экране устарел (views._seen_stale) и проверка rev / seen того же запроса
        'План или настройки изменились после открытия страницы — обновите страницу': 'Էջը բացելուց հետո պլանը կամ կարգավորումները փոխվել են․ պատասխանը չէր համապատասխանի էկրանի պլանին։ Թարմացրեք էջը։',
        'номер плана: ожидалось целое число': BAD_REQUEST,
        'рейсы плана на странице: ожидался список [id, возвращение, опаздывает]': BAD_REQUEST,
    };
    const SERVER_HY_PREFIX = [['машина не готова к расчёту: ', 'Մեքենան պատրաստ չէ հաշվարկի համար՝ ']];
    // StoreError «База настроек маршрутов <файл>: не удалось сохранить …» — по окончанию текста
    const SERVER_HY_SUFFIX = [
        [': не удалось сохранить окно приёма клиента', 'Չհաջողվեց պահպանել ընդունման ժամը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить точку клиента', 'Չհաջողվեց պահպանել խանութի կետը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить план развоза', 'Չհաջողվեց պահպանել առաքման պլանը — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить время у магазина', 'Չհաջողվեց պահպանել ժամանակը խանութում — կարգավորումների բազան հասանելի չէ'],
        [': не удалось сохранить водителя машины', 'Չհաջողվեց պահպանել վարորդին — կարգավորումների բազան հասանելի չէ'],
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
    // act — кнопка в подсказке ({label, run}, «Չեղարկել» после перетаскивания рейса): подсказка держится дольше
    function toast(msg, act) {
        let el = $('dpToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'dpToast';
            el.className = 'dp-toast';
            el.setAttribute('role', 'status');
            $('rtDispatch').appendChild(el);
        }
        el.textContent = msg;
        el.classList.toggle('has-act', !!act);
        if (act) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'dp-toast-act';
            b.textContent = act.label;
            b.addEventListener('click', () => { el.classList.remove('is-on'); act.run(); });
            el.append(' ', b);
        }
        el.classList.add('is-on');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { el.classList.remove('is-on'); const b = el.querySelector('.dp-toast-act'); if (b) b.remove(); }, act ? 9000 : 4200);
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
    // true — день загружен и показан; ошибка (видна в dpLoadError) или ответ на прежний запрос — false
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
            if (seq !== state.loadSeq) return false;
            $('dpBody').hidden = false;     // до отрисовки: карте Leaflet нужен настоящий размер блока, у скрытого он 0×0
            setData(data);
            return true;
        } catch (e) {
            if (seq !== state.loadSeq) return false;
            $('dpLoadErrorText').textContent = e.message;
            $('dpLoadError').classList.remove('d-none');
            return false;
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
        if (!state.data || !data || state.data.day !== data.day) state.progress = null;   // ход дня — своего дня
        setTimeout(loadProgress, 0);
        const trips = new Set();
        if (data.plan) data.plan.trucks.forEach(t => t.trips.forEach(tr => trips.add(tr.id)));
        const hadPlan = !!(state.data && state.data.day === data.day && state.data.plan);
        if (data.day !== state.day) { state.mapFocus = null; state.stepsOpen.clear(); state.agentsPick = null; }
        // рейсы дня появились: до трёх машин — все раскрыты, больше — первая (остальные по нажатию, день виден целиком)
        if (data.plan && !hadPlan) {
            const codes = data.plan.trucks.map(t => t.car_code);
            state.open = new Set(codes.length <= 3 ? codes : codes.slice(0, 1));
        }
        if (data.day !== state.day || !data.plan) state.editing.clear();
        else [...state.editing].forEach(id => { if (!trips.has(id)) state.editing.delete(id); });
        state.data = data;
        state.day = data.day;
        if (aiPanel) aiPanel.onDay(data.day);      // «Հարցրու AI-ին» (routes_dispatch_ai.js): кнопка и разговор дня
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
        // №82: в шапке рабочего экрана — коротко «Այսօր · չրք, 7 հոկտ»
        $('dpTitle').dataset.short = (rel ? rel + ' · ' : '') + (WD_SHORT[wdOf(d.day)] || '') + ', '
            + (isDay(d.day) ? Number(d.day.slice(8, 10)) + ' ' + MONTH_SHORT[Number(d.day.slice(5, 7)) - 1] : '');
        // листок календаря в шапке: день недели, число, месяц и год
        $('dpCalWd').textContent = WD_SHORT[wdOf(d.day)] || '—';
        $('dpCalDay').textContent = isDay(d.day) ? String(Number(d.day.slice(8, 10))) : '–';
        $('dpCalMon').textContent = isDay(d.day) ? MONTH_SHORT[Number(d.day.slice(5, 7)) - 1] + ' ' + d.day.slice(0, 4) : '';
        document.title = 'Առաքում ' + dateRu(d.day) + ' — Sales Dashboard';
        $('dpDate').value = d.day;
        $('dpTomorrow').hidden = d.day === d.default_day;
        renderDateNote();
        renderTrucks();
        renderNoDriver();
        renderOrders();
        renderAgents();
        renderNoCoords();
        renderGeoSug();
        renderOrderLists();
        const plan = d.plan;
        $('dpBuildText').textContent = plan ? 'Վերակազմել երթերը' : 'Կազմել երթերը';
        $('dpReset').hidden = !plan || !!d.approved || !!d.released;   // №80: план у водителей не стирается
        $('dpBuild').disabled = !d.trucks.some(t => t.ready) || !d.depot || !!d.approved;
        // №78: загруженные рейсы закреплены — пересборка их не трогает
        const loadedN = plan ? plan.trucks.reduce((n, t) => n + t.trips.filter(tr => tr.loaded).length, 0) : 0;
        $('dpBuildNote').textContent = d.approved ? APPROVED_HY
            : plan ? 'Ամրացված երթերը կմնան ինչպես կան, մնացածը ծրագիրը կբաշխի նորից։'
                + (loadedN ? ' Բեռնված երթերը (' + loadedN + ') չեն փոխվի։' : '') : 'Մոտ 5 վայրկյան։';
        renderApprove();
        renderSendState();
        renderSteps();
        renderSameDay();
        if ($('dpSameDayDlg').open) { if (sdData()) renderSdDialog(); else $('dpSameDayDlg').close(); }
        $('dpStep3').hidden = !plan;
        if (plan) renderPlan(plan);
        renderInbox();
        renderTabs();
        layoutWs();
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
        const lines = [];
        let btns = [];
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
        } else if (crewOf().stale) {
            tone = 'is-warn'; ico = 'fa-id-card';
            title = 'Վարորդները փոխվել են — վերակազմեք երթերը';
            lines.push('Երթերը կազմված են նախկին վարորդներով։ Ծրագիրը կընտրի այնքան մեքենա, որքան վարորդ կա։');
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
        } else if (state.geoChanged === d.day) {
            tone = 'is-warn'; ico = 'fa-location-dot';
            title = 'Խանութի կետը փոխվել է';
            lines.push(GEO_REBUILD);
            btns.push({ act: build, text: 'Վերակազմել երթերը', ico: 'fa-rotate' });
        } else if (d.unsent && d.sent && !d.is_past) {
            // №81: правки после отправки — водители их не видят, пока не нажать «Ուղարկել»
            tone = 'is-warn'; ico = 'fa-paper-plane';
            title = 'Փոփոխությունները դեռ չեն ուղարկվել վարորդներին';
            lines.push('Վարորդները տեսնում են ' + builtWhen(d.sent.at) + '-ի պլանը։ ' + unsentDetail(d.unsent));
            if (gone) lines.push(gone);
            btns.push({ act: sendPlan, text: 'Ուղարկել վարորդներին', ico: 'fa-paper-plane' });
        } else if (planIssues(plan)) {
            tone = 'is-warn'; ico = 'fa-triangle-exclamation';
            title = 'Երթերը կազմված են, բայց ոչ ամեն ինչ է տեղավորվել';
            lines.push('Ստորև գրված է՝ ինչը չի տեղավորվել և ինչ անել։');
            if (gone) lines.push(gone);
            btns.push({ act: () => showPlanPart(() => $('dpOverflow').firstElementChild || $('dpUnassigned').firstElementChild), text: 'Ցույց տալ', ico: 'fa-arrow-down' });
            btns.push(d.sent || d.is_past ? { act: printSheets, text: 'Տպել վարորդների համար', ico: 'fa-print' }
                : { act: approvePlan, text: 'Հաստատել և ուղարկել', ico: 'fa-paper-plane' });
        } else if (!d.sent && !d.is_past) {
            // №81: следующий шаг после сборки — утвердить: только тогда водители видят рейсы в «Առաքիչ»
            tone = 'is-ok'; ico = 'fa-circle-check';
            title = 'Երթերը պատրաստ են — հաստատեք և ուղարկեք վարորդներին';
            const when = builtWhen(d.built_at);
            lines.push((when ? 'Կազմվել են ' + when + '։ ' : '') + 'Վարորդները կտեսնեն երթերը «Առաքիչ» ծրագրում միայն հաստատելուց հետո։');
            if (coming) lines.push(coming + ' Եթե գան նոր պատվերներ, այստեղ կհայտնվի հիշեցում։');
            if (gone) lines.push(gone);
            btns.push({ act: approvePlan, text: 'Հաստատել և ուղարկել', ico: 'fa-paper-plane' });
            btns.push({ act: printSheets, text: 'Տպել վարորդների համար', ico: 'fa-print' });
        } else {
            tone = 'is-ok'; ico = 'fa-circle-check';
            title = d.sent ? 'Պլանն ուղարկված է վարորդներին — տպեք թերթիկները' : 'Երթերը պատրաստ են — տպեք թերթիկները վարորդների համար';
            const when = d.sent ? builtWhen(d.sent.at) : builtWhen(d.built_at);
            const tail = coming ? coming + ' Եթե գան նոր պատվերներ, այստեղ կհայտնվի հիշեցում։' : '';
            if (when || tail) lines.push(((when ? (d.sent ? 'Ուղարկվել է ' : 'Կազմվել են ') + when + '։ ' : '') + tail).trim());
            if (d.sent && !d.is_past) lines.push('Հետագա փոփոխությունները վարորդները կտեսնեն միայն «Ուղարկել» սեղմելուց հետո։');
            if (gone) lines.push(gone);
            btns.push({ act: printSheets, text: 'Տպել վարորդների համար', ico: 'fa-print' });
            btns.push({ act: exportExcel, text: 'Ներբեռնել Excel', ico: 'fa-file-excel' });
        }
        if (d.approved && btns.some(b => b.act === build)) {
            btns = btns.filter(b => b.act !== build);
            lines.push(APPROVED_HY);
        }
        // магазины без точки на карте в рейсы не попадают — после сборки о них напоминает счётчик «Առանց կետի» (№81, dpInbox)
        // №77: отмеченные машины, которые сегодня без водителя, — в рейсах их нет
        const idle = plan ? d.trucks.filter(t => t.unmanned && t.selected) : [];
        if (idle.length) lines.push('Առանց վարորդի ' + dayHuman(d.day) + 'ն դուրս չեն գալիս՝ ' + idle.map(truckLabel).join(', ') + '։');
        if (d.same_day_unread) lines.push('Ուշադրություն՝ նախորդ օրվա պլանը չկարդացվեց․ պատվերները, որոնք այդ օրը տարվել են նույն օրը, կարող են կրկին լինել այստեղ։ Ստուգեք ցուցակը։');
        if (d.is_past) lines.unshift('Սա անցած օր է՝ դիտելու և համեմատելու համար։');
        if (d.day_off) lines.unshift('Կարգավորումներում այս օրը նշված է որպես ոչ աշխատանքային․ սովորաբար այս օրը առաքում չկա։');
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
            el.dataset.ico = b.ico;
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
        return state.pickCid !== null || state.dragging || !!state.mapDrag || $('dpGeoDlg').open || $('dpUnloadDlg').open || $('dpDriverDlg').open
            || $('dpSameDayDlg').open || $('dpAbsentDlg').open || $('dpSendFirstDlg').open
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
        // новые заказы дня (№72): пришли или ушли — перечитать день, плашка появится без перезагрузки страницы
        const sdBefore = d.same_day ? d.same_day.count : 0;
        const sdChanged = isObj(s.same_day) && isObj(d.same_day) && s.same_day.sig !== d.same_day.sig;
        if ((s.orders_sig !== d.orders_sig || s.rev !== d.rev || sdChanged) && !interacting()) {
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
        const sdAfter = state.data.same_day ? state.data.same_day.count : 0;
        if (sdAfter > sdBefore) announce('Այսօր եկել է ' + pl(sdAfter, 'նոր պատվեր'));
    }

    // ---------- Утверждение плана дня (ответ владельца №73) ----------
    // Одна кнопка закрепляет все рейсы; пока план утверждён, полной пересборки нет (правки и новые заказы дня — есть)
    const APPROVED_HY = 'Պլանը հաստատված է։ Ամբողջական վերակազմման համար նախ չեղարկեք հաստատումը։';
    function renderApprove() {
        const d = state.data, box = $('dpApprove');
        box.innerHTML = '';
        if (!d.plan || (d.is_past && !d.approved)) { box.hidden = true; return; }
        box.hidden = false;
        const a = d.approved;
        if (a) {
            const badge = document.createElement('span');
            badge.className = 'rt-badge b-ok';
            badge.id = 'dpApprovedBadge';
            badge.innerHTML = '<i class="fas fa-lock" aria-hidden="true"></i>';
            const at = typeof a.at === 'string' && a.at.length >= 16
                ? (a.at.slice(0, 10) === d.day ? '' : dayHuman(a.at.slice(0, 10)) + ' ') + 'ժ. ' + a.at.slice(11, 16) : '';
            badge.appendChild(document.createTextNode(['Պլանը հաստատված է', at, a.by || ''].filter(Boolean).join(' · ')));
            box.appendChild(badge);
            if (!d.is_past) {
                const b = document.createElement('button');
                b.type = 'button';
                b.id = 'dpUnapprove';
                b.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                b.innerHTML = '<i class="fas fa-lock-open" aria-hidden="true"></i><span>Չեղարկել հաստատումը</span>';
                b.title = badge.textContent;
                b.addEventListener('click', () => {
                    if (!window.confirm('Չեղարկե՞լ օրվա պլանի հաստատումը։ Հաստատմամբ ամրացված երթերը կապամրացվեն, '
                        + 'ձեր ամրացրածները կմնան։ Դրանից հետո կարելի է վերակազմել երթերը։')) return;
                    edit({ action: 'unapprove' }, 'Պլանի հաստատումը չեղարկված է');
                });
                box.appendChild(b);
            }
            return;
        }
        const b = document.createElement('button');
        b.type = 'button';
        b.id = 'dpApproveBtn';
        // №81: пока план не у водителей, утверждение — главная кнопка сводки (печать рядом — второстепенная)
        b.className = 'rt-btn ' + (d.sent ? 'rt-btn-ghost' : 'rt-btn-primary');
        b.innerHTML = '<i class="fas fa-paper-plane" aria-hidden="true"></i><span></span>';
        b.lastChild.textContent = d.sent ? 'Հաստատել օրվա պլանը' : 'Հաստատել և ուղարկել վարորդներին';
        b.dataset.short = d.sent ? 'Հաստատել' : 'Հաստատել և ուղարկել';   // №82: в шапке рабочего экрана — коротко
        b.setAttribute('aria-label', b.lastChild.textContent);
        b.addEventListener('click', approvePlan);
        const note = document.createElement('p');
        note.className = 'dp-approve-note';
        note.textContent = (d.sent ? (d.unsent ? 'Հաստատելիս վարորդներին կուղարկվեն նաև բոլոր փոփոխությունները։ ' : '')
            : 'Վարորդները կտեսնեն երթերը «Առաքիչ» ծրագրում։ ')
            + 'Բոլոր երթերը կամրացվեն։ Հետո՝ միայն ձեռքով փոփոխություններ և այսօրվա նոր պատվերներ, ամբողջական վերակազմում՝ միայն հաստատումը չեղարկելուց հետո։';
        box.append(b, note);
    }
    const approvePlan = () => edit({ action: 'approve' }, 'Օրվա պլանը հաստատված է և ուղարկված վարորդներին');

    // ---------- Что видят водители (ответ владельца №81, как «Publish changes» у Routific) ----------
    // Утверждение отправляет план на терминалы «Առաքիչ»; правки после него копятся в черновике (unsent ответа дня), пока
    // логист не нажмёт «Ուղարկել» — тогда водители получают план целиком в нынешнем виде (правка send)
    const SENT_OK = 'Փոփոխություններն ուղարկվեցին վարորդներին';
    const onRoad = (u) => (u && Array.isArray(u.on_road) ? u.on_road : []).map(c => { const t = truckBy(c); return t ? truckLabel(t) : c; });
    function sendPlan() {
        const road = onRoad(state.data.unsent);
        if (road.length && !window.confirm(road.join(', ') + (road.length > 1 ? ' մեքենաներն' : ' մեքենան') + ' արդեն բեռնվում է կամ '
            + 'ճանապարհին է՝ վարորդը փոփոխությունը կտեսնի «Առաքիչ»-ում ճանապարհին։ Զանգահարեք նրան։ Ուղարկե՞լ։')) return;
        edit({ action: 'send' }, SENT_OK);
    }
    // «Չեղարկել փոփոխությունները»: черновик — снова тот план, что у водителей (правка discard)
    function discardPlan() {
        const d = state.data;
        if (!window.confirm('Չեղարկե՞լ բոլոր չուղարկված փոփոխությունները։ Պլանը կվերադառնա վարորդներին ուղարկված վիճակին ('
            + builtWhen(d.sent.at) + ')՝ ներառյալ հաստատումը և ամրացումները։')) return;
        edit({ action: 'discard' }, 'Պլանը վերադարձավ վարորդներին ուղարկված վիճակին');
    }
    function unsentTitle(u) {
        return u.trucks.length ? pl(u.trucks.length, 'մեքենայի') + ' փոփոխությունը չի ուղարկվել' : 'Պատվերների փոփոխությունը չի ուղարկվել';
    }
    function unsentDetail(u) {
        const names = u.trucks.map(c => { const t = truckBy(c); return t ? truckLabel(t) : c; });
        const road = onRoad(u);
        return (names.length ? 'Փոխվել են՝ ' + names.join(', ') + '։' : '')
            + (u.orders ? (names.length ? ' ' : '') + 'Փոխվել է պատվերների ընտրությունը։' : '')
            + (road.length ? ' Արդեն ճանապարհին են՝ ' + road.join(', ') + '․ ուղարկելուց հետո զանգահարեք վարորդին։' : '');
    }
    function renderSendState() {
        const d = state.data, box = $('dpSendState');
        const printMain = !!d.sent || !!d.is_past;
        $('dpPrint').classList.toggle('rt-btn-primary', printMain);
        $('dpPrint').classList.toggle('rt-btn-ghost', !printMain);
        box.textContent = '';
        box.hidden = !d.plan || (d.is_past && !d.sent);
        if (box.hidden) return;
        const unsent = !!(d.sent && d.unsent && !d.is_past);
        const pill = document.createElement('span');
        pill.className = 'dp-sendpill ' + (!d.sent ? 'is-draft' : unsent ? 'is-unsent' : 'is-sent');
        pill.innerHTML = '<i class="fas ' + (!d.sent ? 'fa-pen-ruler' : unsent ? 'fa-circle' : 'fa-paper-plane') + '" aria-hidden="true"></i><span></span>';
        pill.lastChild.textContent = !d.sent ? 'Սևագիր — վարորդները դեռ չեն տեսնում'
            : unsent ? unsentTitle(d.unsent) : 'Ուղարկված է վարորդներին · ' + builtWhen(d.sent.at);
        if (unsent) pill.title = unsentDetail(d.unsent);
        box.appendChild(pill);
        if (!unsent) return;
        const b = document.createElement('button');
        b.type = 'button';
        b.id = 'dpSendBtn';
        b.className = 'rt-btn rt-btn-primary rt-btn-sm';
        b.innerHTML = '<i class="fas fa-paper-plane" aria-hidden="true"></i><span>Ուղարկել</span>';
        b.setAttribute('aria-label', 'Ուղարկել փոփոխությունները վարորդներին');
        b.addEventListener('click', sendPlan);
        const x = document.createElement('button');
        x.type = 'button';
        x.id = 'dpDiscardBtn';
        x.className = 'rt-linkbtn dp-discard';
        x.textContent = 'Չեղարկել փոփոխությունները';
        x.addEventListener('click', discardPlan);
        box.append(b, x);
    }

    // ---------- Требует внимания (№81): счётчики одной строкой, нажатие — к месту на странице ----------
    function goTo(el) {
        if (!el) return;
        el.scrollIntoView({ block: 'start', behavior: calm() ? 'auto' : 'smooth' });
        const f = el.matches('details') ? el.querySelector('summary') : el.querySelector('h3, h4, summary, button');
        if (f) f.focus({ preventScroll: true });
    }
    // часть рейсов (не поместились, ещё не в рейсах): на телефоне — сначала вкладка «Երթեր»
    function showPlanPart(find) {
        if (state.tab !== 'trips' && tabsOn()) setTab('trips');
        goTo(find());
    }
    function openFold(id) {
        if (state.tab !== 'day' && tabsOn()) setTab('day');
        if ($('rtDispatch').classList.contains('is-ws') && $('dpDrawer').hidden) openDrawer(true);   // №82: шаг 2 — в панели
        unfoldStep('dpStep2');
        const el = $(id);
        el.open = true;
        goTo(el);
    }
    function renderInbox() {
        const d = state.data, plan = d.plan, box = $('dpInbox');
        box.textContent = '';
        if (!plan) { box.hidden = true; return; }
        const add = (tone, ico, label, count, act, href, id) => {
            const el = document.createElement(href ? 'a' : 'button');
            el.className = 'dp-inchip' + (tone ? ' ' + tone : '');
            if (id) el.id = id;
            if (href) el.href = href; else { el.type = 'button'; el.addEventListener('click', act); }
            el.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span></span>' + (count ? '<b></b>' : '');
            el.querySelector('span').textContent = label;
            if (count) el.querySelector('b').textContent = fmt(count);
            box.appendChild(el);
        };
        const bad = plan.trucks.reduce((n, t) => n + t.trips.filter(tripBad).length, 0);
        if (bad) add('is-bad', 'fa-triangle-exclamation', 'Խնդիր երթերում', bad, () => showPlanPart(() => $('dpOverflow').firstElementChild));
        if (plan.unassigned.length) add('is-bad', 'fa-inbox', 'Երթերում չեն', plan.unassigned.length, () => showPlanPart(() => $('dpUnassigned').firstElementChild));
        // №86: на рабочем экране плашка «Այսօրվա նոր պատվերներ» — счётчиком в этой строке (карта выше); нажатие — тот же диалог
        if (wsOn() && sdData()) {
            const later = sdLater(state.day), inv = sdInvoiced().filter(o => !sdOutside(o));
            const fresh = sdNew().filter(o => !later.has(o.isn));
            const all = sdOpenOrders().length + sdTaken().length;
            if (fresh.length || inv.length) add('is-warn', 'fa-bolt', 'Նոր պատվերներ', fresh.length + inv.length, () => openSameDay(), null, 'dpSdChip');
            else if (all) add('', 'fa-bolt', 'Այսօրվա պատվերներ', all, () => openSameDay(), null, 'dpSdChip');
        }
        const o = d.orders;
        if (o.no_coords) add('is-warn', 'fa-location-dot', 'Առանց կետի', o.no_coords, () => openFold('dpNoCoords'));
        const gs = geoSug();
        if (gs.count) add('is-warn', 'fa-location-crosshairs', 'Նոր տեղ՝ վարորդներից', gs.count, () => openFold('dpGeoSug'));
        const bl = d.backlog || [];
        if (bl.length) add('', 'fa-clock-rotate-left', 'Նախորդ օրերից', bl.length, () => openFold('dpBacklog'));
        const nd = noDriver();
        if (nd.length && !d.is_past && wsOn()) add('is-warn', 'fa-id-card', 'Առանց վարորդի', nd.length, () => openDriver(nd[0].car_code));
        if (o.excluded) add('', 'fa-ban', 'Այսօր չենք տանում', o.excluded, () => openFold('dpExcluded'));
        // нормы погрузки, расхода и износа не заполнены — цифры дня приблизительные (раньше — только серым текстом под сводкой)
        const sm = plan.summary;
        if (sm.loading_configured === false || sm.fuel_load_unconfigured || sm.wear_unconfigured)
            add('is-warn', 'fa-gear', 'Լրացնել մեքենաների նորմերը', 0, null, '/routes/settings#trucks');
        box.hidden = !box.children.length;
    }

    // ---------- Новые заказы дня (ответ владельца №72) ----------
    // Заказ, принятый сегодня, — по правилу развоз следующего дня. Плашка сообщает о нём (и при автообновлении), диалог
    // предлагает, как везти сегодня (POST /api/routes/dispatch/same-day — варианты сервера, самый дешёвый первым); выбор —
    // POST /api/routes/dispatch/edit {action: same_day, orders, option, rev}; «Թողնել վաղվան» у взятого — same_day_drop.
    // «Թողնել վաղվան» у нового ничего не меняет на сервере: заказ остаётся завтрашним, плашка о нём больше не напоминает
    // (localStorage этого браузера, по дню; новый заказ — снова плашка).
    const SD_KIND = {
        same_stop: ['fa-store', 'Նույն խանութին՝ նույն երթով', 'Խանութն արդեն այսօրվա երթում է, որը դեռ չի բեռնվում'],
        insert: ['fa-route', 'Ավելացնել երթին', 'Երթը դեռ չի սկսել բեռնվել, մյուս խանութների հերթականությունը չի փոխվում'],
        trip: ['fa-rotate-left', 'Նոր երթ՝ պահեստ վերադառնալուց հետո', 'Մեքենան վերադառնում է պահեստ, բեռնվում և նորից դուրս գալիս'],
        idle: ['fa-truck', 'Նոր երթ՝ ազատ մեքենայով', 'Մեքենան աշխատող է նշված, բայց այսօր երթ չունի'],
        extra: ['fa-plus', 'Լրացուցիչ մեքենա (այսօր նշված չէ)', 'Այս մեքենան 1-ին քայլում աշխատող նշված չէ — ընտրելիս կնշվի։ Ընտրեք, միայն եթե այն իսկապես կարող է դուրս գալ'],
    };
    const SD_BLOCKED = {
        started: 'մնում է վաղվան — երթն արդեն մեկնել է',
        no_coords: 'խանութի տեղը քարտեզում չկա — նոր պատվերը՝ վաղը',
    };
    const SD_LATER = 'dpSdLater:';
    function sdLater(day) {
        try {
            const v = JSON.parse(window.localStorage.getItem(SD_LATER + day) || '[]');
            return new Set(Array.isArray(v) ? v.filter(x => typeof x === 'string') : []);
        } catch (e) { return new Set(); }
    }
    function setSdLater(day, isns) {
        try {
            // помним только сегодняшний выбор: прошлые дни больше не нужны
            for (let i = window.localStorage.length - 1; i >= 0; i--) {
                const k = window.localStorage.key(i);
                if (k && k.startsWith(SD_LATER) && k !== SD_LATER + day) window.localStorage.removeItem(k);
            }
            window.localStorage.setItem(SD_LATER + day, JSON.stringify([...isns]));
        } catch (e) { /* хранилище браузера недоступно — плашка просто напомнит снова */ }
    }
    const sdData = () => (state.data && isObj(state.data.same_day) && Array.isArray(state.data.same_day.orders)) ? state.data.same_day : null;
    const sdOpenOrders = () => { const sd = sdData(); return sd && sd.today ? sd.orders.filter(o => !o.taken) : []; };
    // накладная уже на сегодня (№72): офис решил, что везут сегодня, — на завтра такой заказ не останется (D+1 считает его
    // отгруженным), поэтому он не «новый» и «Թողնել վաղվան» его не касается: его надо поставить в рейс
    const sdNew = () => sdOpenOrders().filter(o => !o.invoiced);
    const sdInvoiced = () => sdOpenOrders().filter(o => o.invoiced);
    // с накладной, но в рейс не поставить (рейс магазина уехал, нет точки): офис везёт сам — сведение, не предупреждение
    const sdOutside = (o) => o.invoiced && (o.no_coords || o.started);
    const SD_OUTSIDE = 'գնում է այսօր պլանից դուրս (գրասենյակի որոշում)';
    const sdTaken = () => { const sd = sdData(); return sd ? sd.orders.filter(o => o.taken) : []; };
    const sdTotals = (os) => ({ kg: os.reduce((a, o) => a + (num(o.kg) || 0), 0), revenue: os.reduce((a, o) => a + (num(o.revenue) || 0), 0) });
    const sdTruck = (code, name) => (name ? name + ' · ' : '') + code;
    function sdTruckName(code) {
        const t = (state.data.trucks || []).find(x => x.car_code === code);
        return sdTruck(code, t && t.name);
    }

    function renderSameDay() {
        const box = $('dpSameDay');
        const sd = sdData();
        const open = sdNew(), all = sdInvoiced(), taken = sdTaken();
        const inv = all.filter(o => !sdOutside(o)), outside = all.filter(sdOutside);
        const later = sd ? sdLater(state.day) : new Set();
        const fresh = open.filter(o => !later.has(o.isn));
        if (!sd || (!open.length && !all.length && !taken.length)) { box.hidden = true; box.innerHTML = ''; return; }
        let tone = 'is-info', ico = 'fa-bolt', title, btn = null;
        const lines = [];
        const invLine = inv.length ? pl(inv.length, 'պատվերի') + ' հաշիվ-ապրանքագիրն արդեն գրված է՝ գնում է այսօր (' + kgText(sdTotals(inv).kg)
            + ')։ Ավելացրեք դրանք երթերին՝ վաղվա առաքման մեջ դրանք չեն լինի։' : '';
        if (inv.length && !fresh.length) {
            tone = 'is-warn';
            title = pl(inv.length, 'պատվեր') + ' գնում է այսօր, բայց երթերում չէ';
            lines.push(invLine);
            btn = 'Ավելացնել երթերին';
        } else if (fresh.length) {
            const tot = sdTotals(fresh);
            tone = 'is-warn';
            title = 'Այսօր եկել է ' + pl(fresh.length, 'նոր պատվեր') + ' · ' + kgText(tot.kg) + ' · ' + money(tot.revenue);
            lines.push('Ըստ կանոնի դրանք վաղվա առաքման մեջ են։ Կարող եք տանել նաև այսօր՝ ծրագիրը կառաջարկի ամենաէժան տարբերակը, նույնիսկ եթե մեքենաներն արդեն դուրս են եկել։');
            if (invLine) lines.push(invLine);
            btn = 'Ի՞նչ անել դրանց հետ';
        } else if (open.length) {
            title = pl(open.length, 'նոր պատվեր') + ' թողնված է վաղվան';
            btn = 'Դիտել';
        } else if (!taken.length) {
            title = pl(outside.length, 'պատվեր') + ' ' + SD_OUTSIDE;
            btn = 'Դիտել';
        } else {
            tone = 'is-ok'; ico = 'fa-circle-check';
            title = (sd.today ? 'Այսօր' : 'Այդ օրը') + ' ընդունված պատվերներից ' + pl(taken.length, 'պատվեր') + ' տարվում է նույն օրը';
            btn = 'Դիտել';
        }
        if (outside.length && (fresh.length || open.length || inv.length || taken.length))
            lines.push(pl(outside.length, 'պատվեր') + ' ' + SD_OUTSIDE + '։');
        if (taken.length && (fresh.length || open.length || inv.length)) lines.push('Արդեն տանում ենք այսօր՝ ' + pl(taken.length, 'պատվեր') + '։');
        box.className = 'dp-todo dp-sameday ' + tone;
        box.innerHTML = '<div class="dp-todo-ico" aria-hidden="true"><i class="fas ' + ico + '"></i></div>'
            + '<div class="dp-todo-body"><p class="dp-todo-k">Այսօրվա նոր պատվերներ</p><h2 class="dp-todo-t"></h2>'
            + '<div class="dp-todo-lines"></div><div class="dp-todo-btns"></div></div>';
        box.querySelector('.dp-todo-t').textContent = title;
        // №81: плашка — одна строка с кнопкой; пояснения — под «Մանրամասն» (выбор всё равно в диалоге). Заказы с накладной
        // на сегодня — не сворачиваем: не поставить их в рейс — значит не отвезти ни сегодня, ни завтра
        if (lines.length && inv.length) lines.forEach(t => { const p = document.createElement('p'); p.textContent = t; box.querySelector('.dp-todo-lines').appendChild(p); });
        else if (lines.length) {
            const more = document.createElement('details');
            more.className = 'dp-more';
            more.innerHTML = '<summary>Մանրամասն</summary>';
            lines.forEach(t => { const p = document.createElement('p'); p.textContent = t; more.appendChild(p); });
            box.querySelector('.dp-todo-lines').appendChild(more);
        }
        if (btn) {
            const b = document.createElement('button');
            b.type = 'button';
            b.id = 'dpSdOpen';
            b.className = 'rt-btn ' + (fresh.length || inv.length ? 'rt-btn-primary' : 'rt-btn-ghost');
            b.innerHTML = '<i class="fas fa-list-check" aria-hidden="true"></i><span></span>';
            b.lastChild.textContent = btn;
            b.addEventListener('click', () => openSameDay());
            box.querySelector('.dp-todo-btns').appendChild(b);
        }
        box.hidden = false;
    }

    function openSameDay() {
        if (state.busy || !sdData()) return;
        const later = sdLater(state.day);
        const can = sdNew().filter(o => !o.no_coords && !o.started);
        const fresh = can.filter(o => !later.has(o.isn));
        const inv = sdInvoiced().filter(o => !o.no_coords && !o.started);   // с накладной на сегодня — отмечены всегда
        const pick = [...(fresh.length || inv.length ? fresh : can), ...inv];
        state.sd = { pick: new Set(pick.map(o => o.isn)), options: null, blocked: [], left: new Map(), forIsns: null, busy: false };
        $('dpSdErr').textContent = '';
        renderSdDialog();
        if (!$('dpSameDayDlg').open) $('dpSameDayDlg').showModal();
    }

    function sdRow(o, checkable) {
        const li = document.createElement('li');
        const off = o.no_coords || (!o.taken && o.started);   // сегодня не взять: нет точки / рейс магазина уже уехал
        li.className = 'dp-sd-row' + (off ? ' is-off' : '');
        const lead = document.createElement(checkable ? 'input' : 'span');
        if (checkable) {
            lead.type = 'checkbox';
            lead.checked = state.sd.pick.has(o.isn);
            lead.disabled = off || state.sd.busy;
            lead.setAttribute('aria-label', 'Տանել այսօր՝ ' + (o.name || o.code));
            lead.addEventListener('change', () => {
                if (lead.checked) state.sd.pick.add(o.isn); else state.sd.pick.delete(o.isn);
                state.sd.options = null;      // варианты — для прежнего выбора
                state.sd.left.delete(o.isn);
                renderSdDialog();
            });
        } else {
            lead.innerHTML = '<i class="fas fa-truck-fast" aria-hidden="true"></i>';
        }
        const main = document.createElement('div');
        main.className = 'dp-sd-main';
        const b = document.createElement('b');
        b.textContent = o.name || o.code || ('#' + o.customer_id);
        const sub = document.createElement('span');
        sub.className = 'dp-sd-sub';
        sub.textContent = [o.code, o.agent_name || o.agent_code, o.created ? 'ընդունվել է ժամը ' + o.created : '', o.doc_num ? '№' + o.doc_num : '']
            .filter(Boolean).join(' · ');
        main.append(b, sub);
        const tags = document.createElement('span');
        tags.className = 'dp-sd-tags';
        const tag = (cls, text) => { const t = document.createElement('span'); t.className = 'rt-badge ' + cls; t.textContent = text; tags.appendChild(t); };
        if (o.taken) tag('b-ok', 'Տանում է՝ ' + (o.trucks || []).map(sdTruckName).join(', '));
        if (o.taken && o.started) tag('b-warn', 'Մեքենան արդեն ճանապարհին է');
        if (o.invoiced && !o.taken) tag('b-gps', 'Հաշիվ-ապրանքագիրն արդեն գրված է');
        const left = state.sd.left.get(o.isn) || (!o.taken && o.started && !o.no_coords ? 'started' : null);
        if (!o.taken && sdOutside(o)) tag('b-gps', SD_OUTSIDE);   // на завтра он не останется — не «մնում է վաղվան»
        else if (left) tag('b-danger', SD_BLOCKED[left] || left);
        if (o.no_coords) tag('b-danger', 'Տեղը քարտեզում չկա');
        if (!o.taken && !o.invoiced && sdLater(state.day).has(o.isn)) tag('b-warn', 'Թողնված է վաղվան');
        if (tags.children.length) main.appendChild(tags);
        const n = document.createElement('span');
        n.className = 'dp-sd-num';
        n.textContent = kgText(o.kg) + ' · ' + money(o.revenue);
        li.append(lead, main, n);
        if (o.taken && sdData().today && !o.started) {   // уехавший заказ на завтра не вернуть (отвезли бы дважды)
            const drop = document.createElement('button');
            drop.type = 'button';
            drop.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            drop.textContent = 'Թողնել վաղվան';
            drop.disabled = state.sd.busy;
            drop.setAttribute('aria-label', 'Թողնել վաղվան՝ ' + (o.name || o.code));
            drop.addEventListener('click', () => dropSameDay(o));
            li.appendChild(drop);
        }
        return li;
    }

    function sdOption(o, i, names) {
        const best = i === 0 && o.kind !== 'extra';   // машина не из шага 1 лучшей не выделяется (№71: не отмечена — не работает)
        const li = document.createElement('li');
        li.className = 'dp-sd-opt' + (best ? ' is-best' : '');
        const kind = SD_KIND[o.kind] || ['fa-truck', o.kind, ''];
        const t = document.createElement('p');
        t.className = 'dp-sd-opt-t';
        t.innerHTML = '<i class="fas ' + kind[0] + '" aria-hidden="true"></i><span></span>';
        t.lastChild.textContent = (best ? 'Ամենաէժանը՝ ' : '') + kind[1] + ' — ' + sdTruck(o.truck, o.name);
        const when = document.createElement('p');
        when.textContent = 'Բեռնում՝ ' + o.loading_start + ' · մեկնում՝ ' + o.depart + ' · վերադարձ՝ ' + o.return;
        const at = document.createElement('p');
        at.textContent = o.stops.map(s => (names.get(s.customer_id) || '#' + s.customer_id) + '՝ ժամը ' + s.eta).join(' · ');
        const cost = document.createElement('p');
        cost.className = 'dp-sd-cost';
        cost.textContent = '+' + fmt(o.km, 1) + NB + 'կմ · +' + fmt(o.minutes) + NB + 'րոպե · +' + money(o.amd);
        const hint = document.createElement('p');
        hint.className = 'dp-sd-sub';
        hint.textContent = kind[2];
        const pick = document.createElement('button');
        pick.type = 'button';
        pick.className = 'rt-btn ' + (best ? 'rt-btn-primary' : 'rt-btn-ghost');
        pick.textContent = 'Ընտրել';
        pick.disabled = state.sd.busy;
        pick.setAttribute('aria-label', 'Ընտրել՝ ' + t.lastChild.textContent);
        pick.addEventListener('click', () => takeSameDay(o));
        li.append(t, when, at, cost, hint, pick);
        return li;
    }

    function renderSdDialog() {
        const sd = sdData();
        if (!sd || !state.sd) return;
        const box = $('dpSdBody');
        const open = sdOpenOrders(), fresh = sdNew(), inv = sdInvoiced(), taken = sdTaken();
        $('dpSdLead').textContent = sd.today
            ? 'Ժամը ' + sd.now + ' է։ Այս պատվերներն ընդունվել են այսօր և ըստ կանոնի վաղվա առաքման մեջ են։ Նշեք, որոնք տանել այսօր, և սեղմեք «Ցույց տալ տարբերակները»։ '
              + 'Ճանապարհին գտնվող մեքենան նոր պատվեր չի վերցնում՝ նախ վերադառնում է պահեստ և բեռնվում։'
            : 'Այս պատվերներն ընդունվել են այդ օրը և տարվել նույն օրը։';
        box.innerHTML = '';
        const group = (title, orders, label) => {
            if (!orders.length) return;
            if (title) {
                const h = document.createElement('h3');
                h.className = 'dp-sd-h';
                h.textContent = title;
                box.appendChild(h);
            }
            const ul = document.createElement('ul');
            ul.className = 'dp-sd-list';
            ul.setAttribute('aria-label', label);
            orders.forEach(o => ul.appendChild(sdRow(o, true)));
            box.appendChild(ul);
        };
        if (open.length) {
            group(inv.length ? 'Նոր պատվերներ' : '', fresh, 'Նոր պատվերներ');
            group('Հաշիվ-ապրանքագիրն արդեն գրված է — գնում է այսօր', inv, 'Հաշիվ-ապրանքագիրն արդեն գրված է');
            const acts = document.createElement('div');
            acts.className = 'dp-sd-acts';
            const go = document.createElement('button');
            go.type = 'button';
            go.id = 'dpSdPropose';
            go.className = 'rt-btn rt-btn-primary';
            go.disabled = state.sd.busy || !state.sd.pick.size;
            go.innerHTML = '<i class="fas fa-wand-magic-sparkles" aria-hidden="true"></i><span></span>';
            go.lastChild.textContent = state.sd.busy && !state.sd.options ? 'Հաշվում եմ…'
                : 'Ցույց տալ տարբերակները' + (state.sd.pick.size ? ' (' + pl(state.sd.pick.size, 'պատվեր') + ')' : '');
            go.addEventListener('click', proposeSameDay);
            const later = document.createElement('button');
            later.type = 'button';
            later.id = 'dpSdLaterBtn';
            later.className = 'rt-btn rt-btn-ghost';
            later.disabled = state.sd.busy;
            later.hidden = !fresh.length;    // с накладной — не на завтра
            later.innerHTML = '<i class="fas fa-calendar-day" aria-hidden="true"></i><span>Թողնել վաղվան</span>';
            later.addEventListener('click', leaveForTomorrow);
            acts.append(go, later);
            box.appendChild(acts);
        }
        if (state.sd.options) {
            const names = new Map(open.map(o => [o.customer_id, o.name || o.code]));
            const h = document.createElement('h3');
            h.className = 'dp-sd-h';
            h.textContent = state.sd.options.length ? 'Ինչպես տանել այսօր՝ ամենաէժանը վերևում'
                : state.sd.forIsns && state.sd.forIsns.length ? 'Այսօր տանել չի ստացվում' : 'Ընտրված պատվերներից ոչ մեկն այսօր տանել չի կարելի';
            box.appendChild(h);
            if (state.sd.options.length) {
                const ul = document.createElement('ul');
                ul.className = 'dp-sd-opts';
                state.sd.options.forEach((o, i) => ul.appendChild(sdOption(o, i, names)));
                box.appendChild(ul);
            } else if (state.sd.forIsns && state.sd.forIsns.length) {
                const p = document.createElement('p');
                p.className = 'dp-note';
                p.textContent = 'Ոչ մի մեքենա չի հասցնում մինչև աշխատանքային օրվա ավարտը կամ բեռը չի տեղավորվում։ Փորձեք ընտրել ավելի քիչ պատվեր կամ թողեք վաղվան։';
                box.appendChild(p);
            }
        }
        if (taken.length) {
            const h = document.createElement('h3');
            h.className = 'dp-sd-h';
            h.textContent = sd.today ? 'Արդեն տանում ենք այսօր' : 'Տարվել են նույն օրը';
            box.appendChild(h);
            const ul = document.createElement('ul');
            ul.className = 'dp-sd-list';
            taken.forEach(o => ul.appendChild(sdRow(o, false)));
            box.appendChild(ul);
        }
    }

    async function proposeSameDay() {
        if (!state.sd || state.sd.busy || !state.sd.pick.size) return;
        const isns = [...state.sd.pick];
        state.sd.busy = true;
        state.sd.options = null;
        $('dpSdErr').textContent = '';
        renderSdDialog();
        try {
            const r = await api('POST', '/api/routes/dispatch/same-day', { date: state.day, orders: isns });
            if (!state.sd) return;
            Object.assign(state.sd, { options: r.options || [], blocked: r.blocked || [], forIsns: r.orders || [] });
            // заказы, которые сегодня не взять (рейс клиента уже уехал, нет точки), — сняты и помечены: остаются на завтра
            (r.blocked || []).forEach(x => (x.orders || []).forEach(isn => { state.sd.pick.delete(isn); state.sd.left.set(isn, x.reason); }));
        } catch (e) {
            if (state.sd) $('dpSdErr').textContent = e.message;
        } finally {
            if (state.sd) { state.sd.busy = false; renderSdDialog(); }
        }
        const best = $('dpSdBody').querySelector('.dp-sd-opt .rt-btn');
        if (best) best.focus();
    }

    async function sdEdit(body) {
        state.sd.busy = true;
        $('dpSdErr').textContent = '';
        renderSdDialog();
        try {
            const data = await api('POST', '/api/routes/dispatch/edit', { date: state.day, rev: state.data.rev, ...body });
            state.sd.busy = false;
            setData(data);
            return data;
        } catch (e) {
            // №78: заказ дня меняет груз загруженного рейса — спросить и повторить с подтверждением
            if (state.sd && e.data && e.data.loaded_confirm === true && !body.confirm_loaded && window.confirm(e.message)) {
                return sdEdit({ ...body, confirm_loaded: true });
            }
            if (state.sd) { state.sd.busy = false; $('dpSdErr').textContent = e.message; renderSdDialog(); }
            return null;
        }
    }

    async function takeSameDay(o) {
        if (!state.sd || state.sd.busy || !state.sd.forIsns) return;
        const n = state.sd.forIsns.length;
        const data = await sdEdit({ action: 'same_day', orders: state.sd.forIsns, option: o.key });
        if (!data) return;
        $('dpSameDayDlg').close();
        toast(pl(n, 'պատվեր') + ' ավելացվեց այսօրվա առաքմանը՝ ' + sdTruck(o.truck, o.name)
            + (data.delta_km !== undefined && data.delta_km !== null ? ' — ' + deltaText(data.delta_km) : '') + '։');
    }

    async function dropSameDay(o) {
        if (!state.sd || state.sd.busy) return;
        const data = await sdEdit({ action: 'same_day_drop', orders: [o.isn] });
        if (data) toast('«' + (o.name || o.code) + '»՝ նոր պատվերը թողնված է վաղվան։');
    }

    function leaveForTomorrow() {
        if (!state.sd || state.sd.busy) return;
        const open = sdOpenOrders();
        // отмеченные (ничего не отмечено — все) и те, что сегодня не взять: без точки на карте
        const fresh = open.filter(o => !o.invoiced);   // с накладной на сегодня — не на завтра
        const isns = [...new Set([...(state.sd.pick.size ? [...state.sd.pick] : fresh.map(o => o.isn)),
            ...fresh.filter(o => o.no_coords || o.started).map(o => o.isn)])].filter(x => fresh.some(o => o.isn === x));
        const later = sdLater(state.day);
        isns.forEach(x => later.add(x));
        setSdLater(state.day, [...later].filter(x => open.some(o => o.isn === x)));
        $('dpSameDayDlg').close();
        renderSameDay();
        renderInbox();
        toast(pl(isns.length, 'պատվեր') + ' կմնա վաղվա առաքման մեջ։');
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
            sub.textContent = t.ready ? 'տանում է մինչև ' + kgText(t.capacity_kg) + (t.center_ok ? ' · մտնում է կենտրոն' : '')
                + (t.big ? ' · մեծ մեքենա' : '') : 'լրացրեք կարգավորումներում';
            txt.append(nm);
            if (t.name) txt.appendChild(plate);
            txt.appendChild(sub);
            if (t.unmanned && t.selected) {     // №77: отмечена, но сегодня без водителя — в рейсах её нет
                const why = document.createElement('span');
                why.className = 'dp-truck-sub is-warn';
                why.textContent = UNMANNED_HY[t.unmanned] || UNMANNED_HY.absent;
                txt.appendChild(why);
            }
            lab.append(cb, sw, txt);
            box.appendChild(lab);
        });
        renderTruckCount();
        renderCrew();
    }
    const UNMANNED_HY = { absent: 'առանց վարորդի', busy: 'վարորդը վարում է այլ մեքենա', moved: 'վարորդը նստել է այլ մեքենա' };
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
            const open = !plan || state.stepsOpen.has(id) || !$('dpDrawer').hidden;   // №82: в панели шаги открыты
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
            if ($('rtDispatch').classList.contains('is-ws') && $('dpDrawer').hidden) openDrawer(true);   // №82: шаги — в панели
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
        const added = bl.filter(x => x.added && !x.agent_off).length;
        if (bl.length) add('', 'fa-clock-rotate-left', 'Նախորդ օրերից մնացել է ' + pl(bl.length, 'չառաքված պատվեր')
            + (added ? ', որից ' + added + '-ը ավելացրել եք այսօրվա առաքմանը' : '') + '։ Ստուգեք՝ պե՞տք է դրանք տանել այսօր։', 'Դիտել', 'dpBacklog');
        if (o.excluded) add('', 'fa-ban', pl(o.excluded, 'պատվեր') + ' նշել եք «այսօր չենք տանում»։', 'Դիտել', 'dpExcluded');
        if (o.other_vehicle) add('', 'fa-truck-arrow-right', pl(o.other_vehicle, 'պատվեր') + ' գնում է այլ մեքենայով (կարգավորումներով)։', 'Դիտել', 'dpOther');
        const ag = agentStats();
        if (ag.shown) add('', 'fa-user-tie', 'Տանում ենք միայն ' + pl(ag.kept, 'մենեջերի') + ' պատվերները'
            + (ag.offCount ? ', ' + pl(ag.offCount, 'պատվեր') + ' (' + kgText(ag.offKg) + ') այսօր չենք տանում' : '')
            + (agentsDirty() ? ' (ընտրությունը դեռ կիրառված չէ)' : '') + '։', 'Դիտել', 'dpAgents');
        const gs = geoSug();
        if (gs.count) add('is-warn', 'fa-location-crosshairs', 'Վարորդներն առաջարկում են նոր տեղ՝ ' + pl(gs.count, 'խանութի') + ' համար'
            + (gs.day_count ? ', որից ' + gs.day_count + '-ը այս օրվա խանութներ են' : '') + '։ Ստուգեք և ընդունեք կամ մերժեք։', 'Դիտել', 'dpGeoSug');
        attn.hidden = !attn.children.length;
        const info = [];
        if (o.shipped_before) info.push(pl(o.shipped_before, 'պատվեր') + ' արդեն առաքվել է');
        if (o.self_delivery) info.push(pl(o.self_delivery, 'պատվեր') + ' (' + kgText(o.self_delivery_kg) + ') մենեջերն ինքն է տանում');
        // №74: правила настроек — везут другие машины (город-исключение) и клиенты, которых машины не везут
        if (o.other_vehicle) info.push(pl(o.other_vehicle, 'պատվեր') + ' (' + kgText(o.other_vehicle_kg) + ') գնում է այլ մեքենայով');
        if (o.customers_off) info.push(pl(o.customers_off, 'պատվեր') + ' (' + kgText(o.customers_off_kg) + ') չենք տանում՝ կարգավորումներով');
        $('dpOrdersInfo').textContent = info.length ? 'Առաքման մեջ չեն մտնում՝ ' + info.join(', ') + '։' : '';
        // день с планом выбран не по нынешним настройкам (№69, №74)
        $('dpRuleDiff').hidden = !state.data.settings_differ;
        $('dpRuleApply').disabled = state.busy;
    }

    // ---------- «Մենեջերներ»: чьи заказы везём ----------
    // До сборки выбор живёт на странице и уходит со сборкой; после — в плане дня (agents_off), меняется кнопкой «Կիրառել»
    const sameSet = (a, b) => a.size === b.size && [...a].every(x => b.has(x));
    function agentsOff() {
        const p = state.agentsPick;
        return p && p.day === state.day ? p.off : new Set(state.data.agents_off || []);
    }
    function agentsDirty() {
        const p = state.agentsPick;
        return !!p && p.day === state.day && !sameSet(p.off, new Set(state.data.agents_off || []));
    }
    // off может держать и менеджеров без заказов этого дня (правило настроек, №69): показ и проверки — только по списку
    // дня (shown — сколько снятых в нём), сам набор не меняется
    function agentStats() {
        const off = agentsOff(), list = state.data.agents || [];
        const st = { off, shown: 0, total: list.length, kept: 0, keptCount: 0, keptKg: 0, offCount: 0, offKg: 0 };
        list.forEach(a => {
            if (off.has(a.agent_id)) { st.shown++; st.offCount += a.count; st.offKg += a.kg; } else { st.kept++; st.keptCount += a.count; st.keptKg += a.kg; }
        });
        return st;
    }
    // «Նշել բոլորին» / «Հանել բոլոր նշումները» — только менеджеры списка; снятые без заказов дня остаются снятыми
    function pickAllAgents(on) {
        const listed = new Set((state.data.agents || []).map(a => a.agent_id));
        pickAgents(new Set([...[...agentsOff()].filter(id => !listed.has(id)), ...(on ? [] : listed)]));
    }
    function pickAgents(off) {
        state.agentsPick = { day: state.day, off };
        if (!agentsDirty()) state.agentsPick = null;
        renderAgents();
        renderOrders();
    }
    function renderAgents() {
        const list = state.data.agents || [];
        const box = $('dpAgents');
        const st = agentStats();
        // один менеджер — выбирать нечего; фильтр уже снял кого-то — список нужен, чтобы вернуть
        box.hidden = list.length < 2 && !st.shown;
        if (box.hidden) return;
        if (agentsDirty()) box.open = true;
        $('dpAgentsNote').textContent = st.shown ? st.kept + ' / ' + st.total : 'բոլորը՝ ' + st.total;
        const fs = $('dpAgentsList');
        fs.querySelectorAll('.dp-truck').forEach(x => x.remove());
        list.forEach(a => {
            const lab = document.createElement('label');
            lab.className = 'dp-truck dp-agent';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = String(a.agent_id);
            cb.checked = !st.off.has(a.agent_id);
            cb.addEventListener('change', () => {
                const off = new Set(agentsOff());
                if (cb.checked) off.delete(a.agent_id); else off.add(a.agent_id);
                pickAgents(off);
                // список строится заново — фокус клавиатуры вернуть на тот же переключатель
                const again = $('dpAgentsList').querySelector('input[value="' + a.agent_id + '"]');
                if (again) again.focus();
            });
            const txt = document.createElement('span');
            txt.className = 'dp-truck-t';
            const nm = document.createElement('b');
            nm.textContent = a.name || a.code || ('մենեջեր ' + a.agent_id);
            txt.appendChild(nm);
            if (a.area) {
                const line = document.createElement('span');
                line.className = 'dp-truck-sub dp-agent-line';
                line.textContent = a.area;
                txt.appendChild(line);
            }
            const sub = document.createElement('span');
            sub.className = 'dp-truck-sub';
            // новые заказы дня, которые ещё решать (№72, same_day) — отдельно: в заказы развоза они не входят
            const parts = [];
            if (a.count || !a.same_day) parts.push(pl(a.count, 'պատվեր') + ' · ' + kgText(a.kg));
            if (a.same_day) parts.push('այսօրվա նոր՝ ' + pl(a.same_day.count, 'պատվեր') + ' · ' + kgText(a.same_day.kg));
            sub.textContent = (a.name && a.code ? a.code + ' · ' : '') + parts.join(' · ');
            txt.appendChild(sub);
            lab.append(cb, txt);
            fs.appendChild(lab);
        });
        const plan = !!state.data.plan, dirty = agentsDirty();
        let sum = '';
        if (st.shown) {
            sum = 'Տանում ենք՝ ' + pl(st.keptCount, 'պատվեր') + ' · ' + kgText(st.keptKg)
                + '։ Այսօր չենք տանում՝ ' + pl(st.offCount, 'պատվեր') + ' · ' + kgText(st.offKg) + '։';
            if (!st.kept) sum += ' Նշեք գոնե մեկ մենեջեր։';
            else if (!plan) sum += ' Ընտրությունը կկիրառվի «Կազմել երթերը» սեղմելիս։';
        }
        if (plan && dirty && st.kept) sum += (sum ? ' ' : '') + 'Սեղմեք «Կիրառել»՝ երթերը կթարմացվեն։';
        $('dpAgentsSum').textContent = sum;
        // у дня нет плана и выбор не меняли — он из правила настроек (№69)
        $('dpAgentsRule').hidden = !(state.data.agents_from_settings && !dirty);
        $('dpAgentsApplyBox').hidden = !(plan && dirty);
        $('dpAgentsApply').disabled = state.busy || !st.kept;
    }
    // Все сняты — везти нечего: так не собираем и не применяем
    function agentsNoneLeft() {
        const st = agentStats();
        return st.total > 0 && !st.kept;
    }
    async function applyAgents() {
        if (!needPlan() || !agentsDirty()) return;
        if (agentsNoneLeft()) { showActionError(new Error('Նշեք գոնե մեկ մենեջեր։')); return; }
        const was = new Set(state.data.agents_off || []);
        const off = state.agentsPick.off;
        const back = [...was].some(x => !off.has(x));
        const data = await edit({ action: 'agents', off: [...off] }, back
            ? 'Մենեջերների ընտրությունը կիրառվեց․ վերադարձված պատվերները «Դեռ երթերում չեն» ցուցակում են, սեղմեք «Վերակազմել երթերը»'
            : 'Մենեջերների ընտրությունը կիրառվեց');
        if (data) { state.agentsPick = null; renderAgents(); renderOrders(); }
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
    // время или своё время магазина по факту (unload_auto_min); со 2-й стоянки по GPS (unload_visits) — время по GPS (№60).
    // «По факту» — только если отличается от нормы больше, чем на округление: сервер шлёт unload_auto_min до 0,1, норму
    // строки обучения — до 0,01, разница — целые сотые, до 5 сотых — округление (8,37 → 8,4; 8,75 → 8,8: в JS
    // 8,8 − 8,75 = 0,05000000000000071, поэтому не «≥ 0,05»), своё время по факту — от 0,5 мин
    function unloadHint(x, norms) {
        const auto = num(x.unload_auto_min), perStop = num(norms.per_stop_min);
        const empty = auto !== null && perStop !== null && Math.round(Math.abs(auto - perStop) * 100) > 5
            ? minutesText(auto) + ' (ըստ փաստի)' : 'սովորական ' + minutesText(perStop);
        const fact = (num(x.unload_visits) ? ' Ըստ վարորդների GPS-ի՝ այս խանութում արդեն եղել է ' + pl(x.unload_visits, 'բեռնաթափում') + '։' : '')
            // №66: проверка выбрала сглаживание к группе (unload_norms.store_rule) — описать его, а не правило №60
            + (norms.store_rule === 'shrink'
                ? ' Քանի դեռ այս խանութում GPS-ով 2 բեռնաթափում չկա, օգտագործվում է ձեր գրած ժամանակը (դատարկ դաշտով՝ արդեն 1-ին բեռնաթափումը, փոքր կշռով)․'
                    + ' 2-րդ բեռնաթափումից սկսած՝ ծրագիրը GPS-ի ժամանակը հարթեցնում է դեպի նման խանութների ժամանակը (չափը՝ ըստ միջին պատվերի, ցանցերը՝ առանձին)․'
                    + ' որքան շատ են բեռնաթափումները, այնքան մեծ է GPS-ի կշիռը։ Այս կանոնն ընտրել է ստուգումը՝ այն ավելի ճշգրիտ էր։'
                : ' Քանի դեռ այս խանութում GPS-ով 2 բեռնաթափում չկա, օգտագործվում է ձեր գրած ժամանակը․ 2-րդ բեռնաթափումից սկսած՝ ծրագիրը ժամանակը վերցնում է GPS-ից։'
                    + ' Եթե առաջին երկու բեռնաթափումները տևողությամբ շատ են տարբերվում, ծրագիրը սպասում է երրորդին։');
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
            + (o.carried ? ' · տեղափոխված է նախորդ օրից' : '')
            + (o.agent_off ? ' · մենեջերը հանված է «Որ մենեջերների պատվերներն ենք տանում» ցուցակից' : '')
            + (o.agent_name ? ' · ' + o.agent_name : '') + (o.city ? ' · ' + o.city : '');
        t.append(b, s);
        li.append(t);
        if (!btnText) return li;   // только строка — без кнопки («գնում է այլ մեքենայով»)
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        btn.textContent = btnText;
        btn.addEventListener('click', onClick);
        li.append(btn);
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
        // №74: везут другие машины — менеджер и город; вернуть можно только в настройках
        const ov = state.data.other_vehicle || [];
        $('dpOther').hidden = !ov.length;
        $('dpOtherNote').textContent = ov.length ? pl(ov.length, 'պատվեր') : '';
        const ul3 = $('dpOtherList');
        ul3.textContent = '';
        ov.forEach(o => ul3.appendChild(orderLine(o)));
    }

    // ---------- Рейсы ----------
    function deltaText(delta) {
        const v = num(delta);
        if (v === null || Math.abs(v) < 0.05) return 'ճանապարհի երկարությունը չի փոխվել';
        return v > 0 ? 'ճանապարհը երկարեց ' + fmt(v, 1) + NB + 'կմ-ով' : 'ճանապարհը կարճացավ ' + fmt(-v, 1) + NB + 'կմ-ով';
    }

    function renderPlan(plan) {
        const sm = plan.summary, base = plan.baseline;
        statTiles($('dpPlanStats'), [[fmt(sm.trucks), 'մեքենա'], [fmt(sm.trips), 'երթ'], [fmt(sm.stops), 'խանութ'], [kgText(sm.kg), 'քաշը'],
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
            // №81: план не короче обычной развозки — сравнение для руководителя («Ղեկավարի համար»), не в сводке логиста
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
        // №78: погрузка по сезону — одна строка; запас в конце дня
        if (sm.preload === true) costNotes.push('մեքենաները բեռնված են նախորդ երեկոյան՝ առաջին երթն առանց առավոտյան բեռնման');
        else if (sm.preload === false) costNotes.push('ձմեռային սեզոն — բեռնում առավոտյան');
        if (sm.end_reserve_min) costNotes.push('մեքենաները վերադառնում են ոչ ուշ, քան օրվա ավարտից ' + fmt(sm.end_reserve_min) + ' րոպե առաջ');
        if (sm.loading_configured === false) costNotes.push('պահեստում բեռնման ժամանակը դեռ ամբողջությամբ նշված չէ');
        if (sm.fuel_load_unconfigured) costNotes.push(fmt(sm.fuel_load_unconfigured) + ' երթի համար բեռից կախված վառելիքի նորմերը նշված չեն');
        if (sm.wear_unconfigured) costNotes.push(fmt(sm.wear_unconfigured) + ' երթի մաշվածքի արժեքը նշված չէ');
        if (sm.fuel_price_estimated) costNotes.push('դիզելի գինը պայմանական է՝ ' + money(sm.fuel_price_amd) + '/լ');
        if (costNotes.length) {
            // №81: как посчитано — свёрнуто; незаполненные нормы — счётчиком «Լրացնել մեքենաների նորմերը» (dpInbox)
            const more = document.createElement('details');
            more.className = 'dp-costnote';
            more.innerHTML = '<summary>Ինչպես է հաշված ճանապարհը և ծախսը</summary><p></p>';
            const text = costNotes.join('։ ') + '։ Նորմերը լրացրեք մեքենաների կարգավորումներում։';
            more.querySelector('p').textContent = text.charAt(0).toUpperCase() + text.slice(1);
            lead.appendChild(more);
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
        // №81: шкалу можно свернуть — больше места рейсам и карте (помнится в этом браузере)
        const fold = document.createElement('button');
        fold.type = 'button';
        fold.className = 'rt-linkbtn dp-board-fold';
        fold.setAttribute('aria-expanded', String(!state.boardFolded));
        fold.innerHTML = '<i class="fas fa-chevron-' + (state.boardFolded ? 'down' : 'up') + '" aria-hidden="true"></i><span></span>';
        fold.lastChild.textContent = state.boardFolded ? 'Բացել' : 'Ծալել';
        fold.addEventListener('click', () => {
            state.boardFolded = !state.boardFolded;
            try { window.localStorage.setItem('dpBoardFolded', state.boardFolded ? '1' : ''); } catch (e) { /* только на эту страницу */ }
            renderBoard(state.data.plan);
            box.querySelector('.dp-board-fold').focus();
        });
        head.appendChild(fold);
        box.appendChild(head);
        box.classList.toggle('is-folded', state.boardFolded);
        if (state.boardFolded) return;

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
            lab.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span class="dp-blabel-t"><b></b><small></small>'
                + '<span class="dp-blabel-x"><span class="dp-blx-t"></span><i class="dp-blfill"><em></em></i></span></span>';
            lab.querySelector('.rt-dot').style.background = color;
            lab.querySelector('b').textContent = t.name || t.car_code;
            lab.querySelector('small').textContent = t.name ? t.car_code : '';
            lab.querySelector('b').dataset.plate = t.name ? ' · ' + t.car_code : '';
            // №82: загрузка самого полного рейса машины (несколько рейсов — по рейсу, не сумма дня) и число бед
            const pct = Math.max(0, ...t.trips.map(tr => num(tr.load_pct) || 0));
            const bad = t.trips.filter(tripBad).length;
            lab.querySelector('.dp-blx-t').textContent = pl(t.stops, 'կետ') + ' · ' + kgText(t.kg) + ' · ' + pct + '%' + (bad ? ' · ⚠ ' + bad : '');
            lab.querySelector('.dp-blabel-x').insertAdjacentHTML('beforeend', '<span class="dp-blx-pg" hidden></span>');
            const fill = lab.querySelector('.dp-blfill');
            fill.classList.toggle('is-hi', pct >= 90 && pct <= 100);
            fill.classList.toggle('is-over', pct > 100);
            fill.firstChild.style.width = Math.min(100, pct) + '%';
            if (bad) lab.classList.add('has-bad');
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
                const info = (t.trips.length > 1 ? 'Երթ ' + (i + 1) + ' · ' : '') + pl(tr.stops.length, 'կետ') + ' · ' + kgText(tr.kg)
                    + (tr.load_pct !== null && tr.load_pct !== undefined ? ' · ' + tr.load_pct + '%' : '');
                const full = truckLabel(t) + ' · երթ ' + (i + 1) + ' · ' + tr.depart + ' → ' + tr.return + ' · ' + pl(tr.stops.length, 'կետ')
                    + ' · ' + kgText(tr.kg) + ' · ≈ ' + fmt(tr.km) + NB + 'կմ';
                bar.title = full;
                bar.setAttribute('aria-label', info + ' — ' + full + ' — ցույց տալ քարտեզում');
                const bt = document.createElement('span');
                bt.className = 'dp-bar-t';
                bt.textContent = info;
                if (tripBad(tr)) bt.insertAdjacentHTML('afterbegin', '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i>');
                bar.appendChild(bt);
                // засечки — приезд в каждый магазин
                tr.stops.forEach((s, k) => {
                    const e = toMin(s.eta);
                    if (e === null || ret <= dep) return;
                    const tick = document.createElement('i');
                    tick.className = 'dp-tick' + (s.window_miss || s.center_miss || s.vehicle_miss ? ' is-bad' : '');
                    tick.textContent = String(k + 1);      // №82: в варианте А — кружок с номером магазина
                    tick.dataset.cid = String(s.customer_id);
                    tick.title = (k + 1) + '. ' + (s.name || s.code) + ' · ' + (s.eta || '');
                    tick.style.left = ((e - dep) / (ret - dep) * 100).toFixed(2) + '%';
                    bar.appendChild(tick);
                });
                bar.addEventListener('click', () => focusFromBoard(t, tr));
                if (!d.is_past && ret - dep >= span / 40) resizable(bar, track, t, tr, dep, ret, t0, span);
                track.appendChild(bar);
                if (tr.buffer) {   // запас на рейс (№66) — конец полосы: с медианного возвращения до возвращения с запасом
                    const a = toMin(tr.buffer.start);
                    if (a !== null && ret > a) {
                        const z = document.createElement('span');
                        z.className = 'dp-buffer-mark';
                        z.style.left = x(a);
                        z.style.width = w(a, ret);
                        z.title = 'Ժամանակի պաշար՝ ' + minText(tr.buffer.minutes) + ' (' + tr.buffer.start + ' → ' + tr.return + ')';
                        track.appendChild(z);
                    }
                }
                if (tr.lunch) {   // обед — засечка на полосе дня (№61)
                    const a = toMin(tr.lunch.start), b = toMin(tr.lunch.end);
                    if (a !== null) {
                        const z = document.createElement('span');
                        z.className = 'dp-lunch-mark';
                        z.style.left = x(a);
                        z.style.width = w(a, Math.max(b ?? a, a + 3));
                        z.title = lunchText(tr.lunch);
                        track.appendChild(z);
                    }
                }
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
        paintProgress();
        syncFocus();
    }

    // Конец полосы рейса тянут мышью (ответ владельца №59): отпустили раньше — магазины уходят другим машинам, позже —
    // машина берёт магазины других и «ещё не в рейсах» (dispatch._resize); шаг 5 минут, Esc — отмена. С клавиатуры на
    // полосе — Shift+← / Shift+→: на 15 минут
    const RESIZE_STEP = 5, RESIZE_KEY_STEP = 15;
    // пока тянут — что будет, если отпустить (тот же resize на сервере с preview: ничего не сохраняется): «−3 կետ · +18 կմ»
    const RESIZE_PREVIEW_MS = 220;
    const previewText = (p) => (p.stops ? (p.stops > 0 ? '+' : '−') + pl(Math.abs(p.stops), 'կետ') : 'առանց փոփոխության')
        + (p.stops && num(p.delta_km) !== null ? ' · ' + (p.delta_km > 0 ? '+' : p.delta_km < 0 ? '−' : '') + fmt(Math.abs(p.delta_km), 1) + NB + 'կմ' : '');
    // время после полуночи — как у сервера: «00:30 (+1)»
    const clock = (m) => hhmm(m % 1440) + (m >= 1440 ? ' (+' + Math.floor(m / 1440) + ')' : '');
    function resizable(bar, track, t, tr, dep, ret, t0, span) {
        const grip = document.createElement('span');
        grip.className = 'dp-grip';
        grip.setAttribute('aria-hidden', 'true');
        grip.title = 'Քաշեք՝ երթը կարճացնելու (կետերը կանցնեն այլ մեքենաների) կամ երկարացնելու համար';
        bar.appendChild(grip);
        bar.setAttribute('aria-keyshortcuts', 'Shift+ArrowLeft Shift+ArrowRight Control+Z');
        bar.title += '\nԱջ եզրը քաշեք կամ Shift+← / Shift+→՝ կարճացնել կամ երկարացնել երթը, Ctrl+Z՝ չեղարկել';
        let drag = null;
        const place = (m) => {
            drag.at = Math.max(dep, Math.min(t0 + span, Math.round(m / RESIZE_STEP) * RESIZE_STEP));
            bar.style.width = (Math.max(0, drag.at - dep) / span * 100).toFixed(3) + '%';
            const was = drag.shown;
            drag.shown = drag.at;
            const when = drag.at <= dep ? 'ամբողջը՝ այլ մեքենաների' : clock(drag.at);
            drag.tip.textContent = Math.abs(drag.at - ret) < RESIZE_STEP ? when : when + ' · …';
            if (was !== drag.at) {
                clearTimeout(drag.timer);
                if (Math.abs(drag.at - ret) >= RESIZE_STEP) drag.timer = setTimeout(() => previewAt(drag.at, when), RESIZE_PREVIEW_MS);
            }
            drag.tip.style.left = ((drag.at - t0) / span * 100).toFixed(3) + '%';
        };
        const previewAt = (at, when) => {
            if (drag.asking) { drag.next = [at, when]; return; }      // один запрос за раз — потом последнее положение
            drag.asking = true;
            const mine = drag;
            api('POST', '/api/routes/dispatch/edit', { date: state.day, rev: state.data.rev, action: 'resize', trip: tr.id, return: at, preview: true }, 15000)
                .then(d => {
                    if (drag !== mine || drag.at !== at || !isObj(d.preview)) return;
                    // к отпущенному времени не успеть — в подсказке настоящее возвращение
                    const late = at < ret && d.preview.return && toMin(d.preview.return) > at ? ' · վերադարձ ' + d.preview.return : '';
                    drag.tip.textContent = when + ' · ' + previewText(d.preview) + late;
                })
                .catch(() => { if (drag === mine && drag.at === at) drag.tip.textContent = when; })
                .finally(() => {
                    mine.asking = false;
                    const next = mine.next;
                    mine.next = null;
                    if (drag === mine && next && next[0] === drag.at) previewAt(...next);
                });
        };
        const finish = (commit) => {
            if (!drag) return;
            clearTimeout(drag.timer);
            const at = drag.at;
            drag.tip.remove();
            document.removeEventListener('keydown', drag.esc, true);
            document.removeEventListener('pointerup', drag.up, true);
            drag = null;
            state.dragging = false;
            bar.classList.remove('is-drag');
            bar.style.width = (Math.max(0, ret - dep) / span * 100).toFixed(3) + '%';
            if (commit && Math.abs(at - ret) >= RESIZE_STEP) resizeTrip(t, tr, at);
        };
        grip.addEventListener('pointerdown', (e) => {
            if (state.busy || e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
            grip.setPointerCapture(e.pointerId);
            const tip = document.createElement('span');
            tip.className = 'dp-drag-tip';
            tip.setAttribute('aria-hidden', 'true');
            track.appendChild(tip);
            // шкалу перерисовали посреди перетаскивания (ручки уже нет на странице) — Esc больше не перехватывается
            drag = { box: track.getBoundingClientRect(), at: ret, shown: ret, timer: null, asking: false, next: null, tip, esc: (k) => { if (k.key === 'Escape' && grip.isConnected) { k.preventDefault(); finish(false); } else if (!grip.isConnected) finish(false); } };
            drag.up = () => { if (!grip.isConnected) finish(false); };
            document.addEventListener('pointerup', drag.up, true);
            state.dragging = true;
            document.addEventListener('keydown', drag.esc, true);
            bar.classList.add('is-drag');
            place(ret);
        });
        grip.addEventListener('pointermove', (e) => {
            if (drag) place(t0 + (e.clientX - drag.box.left) / drag.box.width * span);
        });
        grip.addEventListener('pointerup', () => finish(true));
        grip.addEventListener('pointercancel', () => finish(false));
        grip.addEventListener('lostpointercapture', () => finish(false));
        // отпустили над ручкой — клик по полосе (фокус карты) не нужен
        grip.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
        bar.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.key === 'Z') && canUndo()) { e.preventDefault(); undoResize(); return; }
            if (!e.shiftKey || e.altKey || e.ctrlKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') || state.busy) return;
            e.preventDefault();
            resizeTrip(t, tr, Math.max(Math.min(ret, dep + RESIZE_STEP), ret + (e.key === 'ArrowLeft' ? -RESIZE_KEY_STEP : RESIZE_KEY_STEP)), true);
        });
    }

    // «Չեղարկել» — только того переноса, после которого показана: тот же день и номер правки плана (state.undo); после
    // другой правки, в другой вкладке или в другом дне — нечего отменять
    const canUndo = () => !!state.undo && !state.busy && state.undo.day === state.day && !!state.data && state.undo.rev === state.data.rev;
    function undoResize() {
        if (!canUndo()) { toast('Պլանն արդեն փոխվել է՝ չեղարկելու բան չկա։'); return; }
        state.undo = null;
        edit({ action: 'undo' }, 'Չեղարկվեց');
    }

    async function resizeTrip(t, tr, at, keyboard) {
        const code = t.car_code, ret = toMin(tr.return);
        const stopsOf = (plan) => (plan.trucks.find(x => x.car_code === code) || { stops: 0 }).stops;
        const before = stopsOf(state.data.plan);
        const data = await edit({ action: 'resize', trip: tr.id, return: at }, null);
        if (!data || !data.plan) return;
        const diff = stopsOf(data.plan) - before;
        const cur = data.plan.trucks.flatMap(x => x.trips).find(x => x.id === tr.id);
        const shrink = at < ret;
        // с клавиатуры — фокус на ту же полосу (шкала перерисована)
        const back = keyboard && $('dpBoard').querySelector('.dp-bar[data-trip="' + tr.id + '"]');
        if (back) back.focus();
        let text;
        if (!diff) text = shrink ? 'Կետերը տեղափոխել չհաջողվեց՝ մյուս մեքենաները չեն հասցնի կամ տեղ չունեն'
            : 'Ավելացնելու կետ չգտնվեց՝ մեքենան չի հասցնի մինչև ' + clock(at) + ' կամ մինչև աշխատանքային օրվա վերջը';
        else {
            text = shrink ? truckLabel(t) + '՝ ' + pl(-diff, 'կետ') + ' անցավ այլ մեքենաների' : truckLabel(t) + '՝ ավելացավ ' + pl(diff, 'կետ');
            const late = cur ? toMin(cur.return) : null;
            if (shrink && late !== null && late > at) text += ', վերադարձ ' + cur.return + ' (ավելի շուտ չի ստացվում)';
            text += ' — ' + deltaText(data.delta_km);
        }
        state.undo = diff ? { day: state.day, rev: data.rev } : null;
        toast(text + '։', diff ? { label: 'Չեղարկել', run: undoResize } : null);
    }

    // Выбранное на карте — подсвечено и на шкале, и в карточках машин
    function syncFocus() {
        const f = state.mapFocus;
        $('dpBoard').querySelectorAll('.dp-blabel').forEach(b => b.setAttribute('aria-pressed', String(!!f && f.truck === b.dataset.truck && f.trip == null)));
        $('dpBoard').querySelectorAll('.dp-bar').forEach(b => b.setAttribute('aria-pressed', String(!!f && f.trip != null && String(f.trip) === b.dataset.trip)));
        $('dpTruckCards').querySelectorAll('.dp-tcard').forEach(c => c.classList.toggle('is-focus', !!f && f.truck === c.dataset.truck));
        if ($('rtDispatch').classList.contains('is-ws')) { $('dpWsSide').hidden = !f; $('dpWs').classList.toggle('has-side', !!f); }
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
        if (target && $('rtDispatch').classList.contains('is-ws')) {   // №82: прокручивается только панель машины, не страница
            const side = $('dpWsSide');
            side.scrollTo({ top: target.getBoundingClientRect().top - side.getBoundingClientRect().top + side.scrollTop - 8, behavior: calm() ? 'auto' : 'smooth' });
        } else if (target) target.scrollIntoView({ behavior: calm() ? 'auto' : 'smooth', block: 'start' });
    }

    // Совет «как поместить» (ответ владельца №54; plan.advice — dispatch._advice): отмеченные машины без рейсов — пересобрать;
    // неотмеченная готовая машина — одной кнопкой отметить её в шаге 1 и пересобрать; добавить нечего — «все машины уже
    // отмечены»; опаздывающие закреплённые рейсы (pinned_late) пересборка не меняет — снять закрепление или перенести
    // магазины. Сама сборка машин не добавляет (№32).
    // { kind: rebuild | add | none (no_free) | pinned (только закреплённые), text — со строчной буквы (после «Ինչ անել՝ »),
    //   button() — новая кнопка (rebuild, add),
    //   forCenter, pinned — совет про закреплённые рейсы или '' }; совета нет — null: карточки пишут прежние тексты
    const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
    function adviceButton(ico, text, act) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-stepbtn';
        b.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span></span>';
        b.lastChild.textContent = text;
        b.addEventListener('click', act);
        return b;
    }
    // «Ավելացնել և վերակազմել»: отметить машину в шаге 1 и пересобрать рейсы; галочки нет (машину сняли с расчёта
    // после загрузки страницы) — ошибка, а не сборка без неё
    function addTruckAndBuild(code) {
        if (state.busy) return;
        const cb = [...$('dpTrucks').querySelectorAll('input[type="checkbox"]')].find(x => x.value === code);
        if (!cb || cb.disabled) { showActionError(new Error('Մեքենան՝ ' + truckLabel(truckBy(code)) + ', 1-ին քայլում նշել հնարավոր չէ — թարմացրեք էջը։')); return; }
        cb.checked = true;
        renderTruckCount();
        build();
    }
    function planAdvice(plan) {
        const a = plan.advice;
        if (!isObj(a)) return null;
        const ids = Array.isArray(a.pinned_late) ? a.pinned_late : [], late = [];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => { if (ids.includes(tr.id)) late.push(truckLabel(t) + ', երթ ' + (i + 1)); }));
        const pinned = !late.length ? ''
            : late.length === 1 ? 'ուշացող երթը՝ ' + late[0] + ', ամրացված է, և «Վերակազմել երթերը» այն չի փոխի։ Ապամրացրեք այն կամ «Փոփոխել» կոճակով տեղափոխեք խանութներն այլ երթ։'
            : 'ուշացող երթերը՝ ' + late.join('; ') + ', ամրացված են, և «Վերակազմել երթերը» դրանք չի փոխի։ Ապամրացրեք դրանք կամ «Փոփոխել» կոճակով տեղափոխեք խանութներն այլ երթ։';
        // принятая переработка («Տանել … հետո»): пересборка собирает новый черновик без неё (dispatch.build)
        const reset = state.data.overtime_ok ? ' Վերակազմելիս արտաժամյա աշխատանքը կչեղարկվի։' : '';
        if (Array.isArray(a.rebuild) && a.rebuild.length) {
            const one = a.rebuild.length === 1, names = a.rebuild.map(c => truckLabel(truckBy(c))).join(', ');
            return {
                kind: 'rebuild', forCenter: false, pinned,
                text: 'սեղմեք «Վերակազմել երթերը»։ Նշված ' + (one ? 'մեքենան՝ ' + names + ', այս պլանում երթ չունի' : 'մեքենաները՝ ' + names + ', այս պլանում երթ չունեն')
                    + ' — ծրագիրը բեռը կբաշխի նաև ' + (one ? 'դրա' : 'դրանց') + ' վրա։' + reset,
                button: () => adviceButton('fa-rotate', 'Վերակազմել երթերը', build),
            };
        }
        const t = a.add;
        // добавить нечего: все готовые машины уже отмечены (no_free) или опаздывают только закреплённые рейсы
        if (!isObj(t) || typeof t.car_code !== 'string') return { kind: a.no_free === true ? 'none' : 'pinned', forCenter: false, pinned, text: '', noDrivers: a.no_drivers === true };
        // тоннаж меньше груза, что не поместился (need_kg), — возьмёт только часть; остальное покажет пересборка
        const part = num(t.need_kg) !== null && num(t.capacity_kg) !== null && t.capacity_kg < t.need_kg;
        return {
            kind: 'add', forCenter: !!t.for_center, pinned,
            text: 'սեղմեք «Ավելացնել և վերակազմել»։ Չնշված մեքենան՝ ' + truckLabel(truckBy(t.car_code)) + ' (տանում է մինչև ' + kgText(t.capacity_kg)
                + (t.for_center ? ', մտնում է կենտրոն' : '') + '), կարող է վերցնել ' + (part ? 'բեռի մի մասը։' : 'այս բեռը։') + reset,
            button: () => adviceButton('fa-plus', 'Ավելացնել և վերակազմել', () => addTruckAndBuild(t.car_code)),
        };
    }

    // Рейс не поместился и ему поможет пересборка или ещё машина: позже конца дня (кроме закреплённого — пересборка его не
    // меняет), тяжелее тоннажа, машина не работает. Тогда кнопка совета — в карточке «Չի տեղավորվել», карточки ниже
    // (dpUnassigned) её не повторяют
    const stuckOf = (plan) => plan.trucks.some(t => t.trips.some(tr => (tr.over_time && !tr.pinned) || tr.over_capacity || tr.no_truck));

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
        const stuck = stuckOf(plan);
        const winMiss = plan.trucks.some(t => t.trips.some(tr => tr.window_miss));
        const cenMiss = plan.trucks.some(t => t.trips.some(tr => tr.center_miss));
        const vehicleMiss = plan.trucks.some(t => t.trips.some(tr => tr.vehicle_miss));
        // что делать — по виду беды: не поместилось / не успевает к окну / центр на машине без права въезда;
        // не поместилось и есть совет сервера (plan.advice) — какую машину загрузить, кнопкой; добавить нечего — «все
        // машины уже отмечены» (без шага 1); опаздывает закреплённый рейс — снять закрепление или перенести магазины
        const adv = notFit ? planAdvice(plan) : null, act = adv && adv.button ? adv : null, noFree = !!adv && adv.kind === 'none';
        const advice = [];
        if (stuck) advice.push(act ? act.text + (act.kind === 'add' ? ' Կամ սեղմեք «Փոփոխել» երթի մոտ և տեղափոխեք խանութներն այլ երթ։' : '')
            : noFree ? (adv.noDrivers ? 'ազատ վարորդ չկա, և ավելի շատ մեքենա դուրս գալ չի կարող' : 'բոլոր մեքենաներն արդեն նշված են')
                + ', բայց չեն հասցնում մինչև ' + endOfDay() + '-ը։ Սեղմեք «Փոփոխել» երթի մոտ և տեղափոխեք խանութներն այլ երթ։'
            : '1-ին քայլում նշեք ևս մեկ մեքենա և սեղմեք «Վերակազմել երթերը», կամ սեղմեք «Փոփոխել» երթի մոտ և տեղափոխեք խանութներն այլ երթ։');
        if (adv && adv.pinned) advice.push(adv.pinned);
        if (winMiss) advice.push('ընդունման ժամին չհասցնող խանութի մոտ սեղմեք «Փոփոխել» և տեղափոխեք այն այլ երթ կամ մեքենա, որը կհասցնի, '
            + 'կամ նշեք «Այսօր չենք տանում»՝ կտանենք հաջորդ օրը։ Եթե ժամը օրվա վերջում է, կարող եք տանել ' + state.data.work_end + '-ից հետո։');
        if (cenMiss) advice.push('կենտրոնի խանութները տեղափոխեք կենտրոն մտնող մեքենայի երթ (նշեք այդ մեքենան 1-ին քայլում)։');
        if (vehicleMiss) advice.push('խանութը տեղափոխեք թույլատրված մեքենային կամ վերակազմեք երթերը։ Ամրացված անհամապատասխան երթի մեքենան փոխեք կամ նախ ապամրացրեք այն։');
        div.innerHTML = '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i><div class="rt-alert-text"><b>'
            + (notFit ? 'Չի տեղավորվել' : 'Ուշադրություն') + '</b><ul></ul></div>';
        const ul = div.querySelector('ul');
        bad.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
        advice.forEach((t, i) => {
            const p = document.createElement('p');
            p.className = 'dp-problem-do';
            p.textContent = 'Ինչ անել՝ ' + t;
            div.querySelector('.rt-alert-text').appendChild(p);
            if (i === 0 && stuck && act) div.querySelector('.rt-alert-text').appendChild(act.button());   // кнопка — под своим советом
        });
        if ((stuck && !act && !noFree) || cenMiss) div.querySelector('.rt-alert-text').appendChild(stepButton('dpStep1', 'Բացել 1-ին քայլը'));
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
        li.dataset.cid = String(stop.customer_id);
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
        // большая машина в зоне Еревана (№68): к разгрузке прибавлены минуты — они уже во времени точки и рейса
        if (num(stop.yerevan_min)) {
            tag('b-warn', 'Մեծ մեքենա Երևանում՝ +' + fmt(stop.yerevan_min) + NB + 'ր', 'fa-truck-moving');
            tags.lastChild.title = 'մեծ մեքենան Երևանի գոտում ավելի երկար է կանգնում՝ կայանում, մանևր';
        }
        if (stop.vehicle_access) tag(stop.vehicle_miss ? 'b-danger' : 'b-warn',
            (stop.vehicle_miss ? 'Մեքենան չի կարող սպասարկել · ' : '') + vehicleText(stop.vehicle_access, true), 'fa-truck');
        const src = COORD_HY[stop.coord_source];
        if (src) tag(src[0], src[1], 'fa-location-dot');
        // новый заказ дня, взятый в сегодняшний развоз (№72)
        const sdIsns = new Set(sdTaken().map(o => o.isn));
        if ((stop.orders || []).some(o => sdIsns.has(o.isn))) tag('b-ok', 'Այսօրվա նոր պատվեր', 'fa-bolt');
        if (tags.children.length) main.appendChild(tags);
        const kg = document.createElement('span');
        kg.className = 'dp-stop-kg';
        kg.textContent = kgText(stop.kg) + (stop.share > 1 ? ' (1/' + stop.share + ')' : '');
        li.append(eta, no, main, kg);
        draggableStop(li, stop, tripId);
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

    // №81: магазин тянут мышью (только мышь — на телефоне остаётся «Տեղափոխել այլ երթ…») на полосу рейса шкалы или в
    // рейс карточки — правка move, как выбор рейса в списке; на свой рейс и машине, которой магазин не разрешён, — нельзя
    const finePointer = () => !!(window.matchMedia && window.matchMedia('(pointer: fine)').matches);
    function draggableStop(li, stop, tripId) {
        if (state.data.is_past || !finePointer()) return;
        li.draggable = true;
        li.title = 'Քաշեք մկնիկով այլ մեքենայի քարտի կամ երթի վրա';
        li.addEventListener('dragstart', (e) => {
            if (state.busy) { e.preventDefault(); return; }
            state.dragStop = { stop, from: tripId };
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', stop.name || stop.code || '');
            li.classList.add('is-dragged');
            $('rtDispatch').classList.add('is-dragstop');
        });
        li.addEventListener('dragend', () => {
            state.dragStop = null;
            li.classList.remove('is-dragged');
            $('rtDispatch').classList.remove('is-dragstop');
            document.querySelectorAll('#dpBody .is-drop').forEach(x => x.classList.remove('is-drop'));
        });
    }
    // цель — полоса рейса на шкале, рейс в раскрытой карточке или шапка карточки машины: у машины один рейс — он,
    // несколько — карточка раскрывается, пока над ней держат магазин (SPRING_MS), и магазин кладут на нужный рейс
    const SPRING_MS = 600;
    function dropTrip(e) {
        const ds = state.dragStop;
        if (!ds || !e.target.closest) return null;
        let el = e.target.closest('.dp-bar[data-trip], .dp-trip[data-trip]'), id = el ? Number(el.dataset.trip) : null;
        if (!el) {
            el = e.target.closest('.dp-tcard[data-truck]:not(.is-unmanned)');
            const t = el && state.data.plan.trucks.find(x => x.car_code === el.dataset.truck);
            if (!t) return null;
            if (t.trips.length !== 1) return { el, id: null, t, spring: true };
            id = t.trips[0].id;
        }
        const t = state.data.plan.trucks.find(x => x.trips.some(tr => tr.id === id));
        return t && id !== ds.from && vehicleAllowed(ds.stop, t.car_code) ? { el, id, t } : null;
    }
    // раскрыть карточку на месте (без перерисовки списка — иначе пропал бы перетаскиваемый магазин)
    function springOpen(card, t) {
        const body = card.querySelector('.dp-tbody'), head = card.querySelector('.dp-thead');
        if (!body || !body.hidden) return;
        state.open.add(t.car_code);
        t.trips.forEach((tr, i) => body.appendChild(tripBlock(t, tr, i)));
        body.hidden = false;
        if (head) head.setAttribute('aria-expanded', 'true');
    }
    function dropStops() {
        const root = $('dpBody');   // №82: шкала и карточки на рабочем экране — вне #dpStep3
        let spring = null;
        const stopSpring = () => { if (spring) { clearTimeout(spring.timer); spring = null; } };
        root.addEventListener('dragover', (e) => {
            if (!state.dragStop) return;
            const hit = dropTrip(e);
            root.querySelectorAll('.is-drop').forEach(x => { if (!hit || x !== hit.el) x.classList.remove('is-drop'); });
            if (hit && hit.spring) {
                if (!spring || spring.el !== hit.el) {
                    stopSpring();
                    spring = { el: hit.el, timer: setTimeout(() => { springOpen(hit.el, hit.t); spring = null; }, SPRING_MS) };
                }
                e.preventDefault();
                e.dataTransfer.dropEffect = 'none';
                return;
            }
            if (!hit || !spring || !spring.el.contains(hit.el)) stopSpring();
            if (!hit) { e.dataTransfer.dropEffect = 'none'; return; }
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            hit.el.classList.add('is-drop');
        });
        root.addEventListener('dragend', stopSpring);
        // источник мог исчезнуть при перерисовке (тогда его dragend не придёт) — забываем перетаскивание на любом конце
        document.addEventListener('dragend', () => { state.dragStop = null; $('rtDispatch').classList.remove('is-dragstop'); });
        document.addEventListener('drop', () => { state.dragStop = null; $('rtDispatch').classList.remove('is-dragstop'); });
        root.addEventListener('drop', (e) => {
            stopSpring();
            const hit = dropTrip(e), ds = state.dragStop;
            state.dragStop = null;
            if (!hit || hit.spring) return;
            e.preventDefault();
            hit.el.classList.remove('is-drop');
            const n = hit.t.trips.findIndex(tr => tr.id === hit.id) + 1;
            edit({ action: 'move', customer_id: ds.stop.customer_id, from_trip: ds.from, to_trip: hit.id, truck: null },
                '«' + (ds.stop.name || ds.stop.code) + '» տեղափոխվեց՝ ' + truckLabel(hit.t) + ', երթ ' + n);
        });
    }

    function stopList(stops, tripId, editing, lunch) {
        const ol = document.createElement('ol');
        ol.className = 'dp-stoplist';
        if (lunch && lunch.after_stop === null) ol.appendChild(lunchItem(lunch));
        stops.forEach((s, i) => {
            ol.appendChild(stopItem(s, i + 1, tripId, editing));
            if (lunch && lunch.after_stop === i) ol.appendChild(lunchItem(lunch));
        });
        return ol;
    }

    // Обед в пути (№61): где программа вставила паузу — строка расписания рейса «Ճաշ 13:05–13:35»; where — store (после
    // разгрузки), depot (на складе до загрузки), road (в дороге: к концу окна обеда удобного места не было)
    function lunchText(l) {
        const rest = l.minutes - l.added_min;
        const where = l.where || (l.after_stop === null ? 'depot' : 'store');
        return 'Ճաշ ' + (l.end === l.start ? l.start : l.start + '–' + l.end)
            + (where === 'depot' ? ' · պահեստում' : where === 'road' ? ' · ճանապարհին'
                : rest >= 0.5 ? ' (և ' + fmt(rest) + NB + 'րոպե՝ ընդունման ժամին սպասելիս)' : '');
    }
    function lunchItem(l) {
        const li = document.createElement('li');
        li.className = 'dp-stop dp-lunch';
        li.innerHTML = '<span class="dp-stop-eta"></span><span class="dp-num" aria-hidden="true"><i class="fas fa-utensils"></i></span>'
            + '<div class="dp-stop-main"><b></b></div><span class="dp-stop-kg"></span>';
        li.firstChild.textContent = l.start;
        li.querySelector('b').textContent = lunchText(l);
        return li;
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
        // совет сервера (ответ владельца №54): какую свободную машину отметить и пересобрать — кнопкой; кнопка одна на
        // страницу — в первой карточке с советом («Չի տեղավորվել» выше или первая из карточек ниже), текст — во всех
        const adv = planAdvice(plan), act = adv && adv.button ? adv : null;    // совет с действием: пересобрать / добавить
        let advShown = stuckOf(plan);
        const advButton = () => { if (advShown) return []; advShown = true; return [act.button()]; };
        if (noCenter.length) {
            const can = state.data.trucks.filter(t => t.ready && t.center_ok && !t.selected).map(truckLabel);
            const forCenter = act && act.forCenter;
            const card = reasonCard(noCenter, 'fa-city', 'Կենտրոն՝ այսօր չկա թույլատրված մեքենա', [
                'Այս խանութները փոքր կենտրոնում են, իսկ այսօր նշված մեքենաներից ոչ մեկը չի կարող մտնել կենտրոն։',
                forCenter ? cap(act.text)
                    : can.length ? 'Կենտրոն մտնում է՝ ' + can.join(', ') + '։ Նշեք այն 1-ին քայլում և սեղմեք «Վերակազմել երթերը»։'
                    : 'Որ մեքենաները կարող են մտնել կենտրոն, նշվում է կարգավորումներում։',
                'Կամ որոշեք ձեռքով՝ «Այսօր չենք տանում» կամ ավելացրեք որևէ երթի։']);
            if (forCenter) advButton().forEach(b => card.insertBefore(b, card.querySelector('.dp-stoplist')));
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
                    + state.data.overtime_end + '-ը։ ' + (act ? cap(act.text) + ' Կամ նշեք' : 'Նշեք') + ' «Այսօր չենք տանում»՝ դրանք կանցնեն հաջորդ օրվան։' + monthText;
            } else if (act && act.kind === 'rebuild') {
                lead.textContent = cap(act.text) + ' Կամ ավելացրեք խանութը որևէ երթի ձեռքով։';
            } else if (unpicked || changed) {
                lead.textContent = 'Ընտրված մեքենաները չեն հասցնի այս խանութներին առաքել մինչև ' + state.data.work_end + '-ը։ '
                    + (act ? cap(act.text) + ' ' : unpicked ? 'Կա ևս ' + pl(unpicked, 'մեքենա') + '՝ չնշված։ Նշեք 1-ին քայլում և սեղմեք «Վերակազմել երթերը»։ ' : '')
                    + 'Կամ ավելացրեք խանութը որևէ երթի ձեռքով։';
            } else {
                lead.textContent = 'Բոլոր մեքենաներն արդեն նշված են, բայց չեն հասցնում մինչև ' + state.data.work_end + '-ը։';
            }
            card.append(lead, ...(act ? advButton() : []), ...overtimeBlock('Եթե այս պատվերները պետք է տանել այսօր, մեքենաները կաշխատեն '
                + state.data.work_end + '-ից հետո՝ մինչև ' + state.data.overtime_end + '-ը։'));
            if (unpicked && !act) card.appendChild(stepButton('dpStep1', 'Բացել 1-ին քայլը'));
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
            // водитель — кто сегодня за рулём (№77: посаженный сборкой — «փոխարինում»), и առաքիչ — каждый своей строкой
            const seat = crewTruck(t.car_code);
            const crew = ROLES.filter(r => (r === 'driver' && seat ? seat.name : driverOf(t.car_code, r)));
            const warn = seat && seat.warn !== 'none' ? seat.warn : null;   // «не указан» — одной строкой вверху (renderNoDriver)
            if (crew.length || warn) {
                const dr = document.createElement('span');
                dr.className = 'dp-tdriver';
                crew.forEach(r => {
                    const p = document.createElement('span');
                    p.className = 'dp-tperson';
                    p.textContent = r === 'driver' && seat ? CREW[r].word + '՝ ' + seat.name + (seat.seat ? ' (փոխարինում)' : isSub(t.car_code, r) ? ' (փոխարինող)' : '')
                        : CREW[r].word + '՝ ' + driverOf(t.car_code, r) + (isSub(t.car_code, r) ? ' (փոխարինող)' : '');
                    dr.appendChild(p);
                });
                if (warn) {
                    const w = document.createElement('span');
                    w.className = 'dp-tperson dp-crew-warn is-bad';
                    w.textContent = CREW_WARN_HY[warn] || '';
                    dr.appendChild(w);
                }
                head.querySelector('.dp-thead-name').appendChild(dr);
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
            // справа от шапки — накладная машины «Բեռնագիր» (№57): кнопки видны и у свёрнутой карточки
            const top = document.createElement('div');
            top.className = 'dp-ttop';
            top.append(h, waybillActs(t));
            card.append(top, body);
            box.appendChild(card);
        });
        // отмеченные машины без водителя сегодня (№77) — серой карточкой с причиной, без рейсов
        state.data.trucks.filter(t => t.unmanned && t.selected).forEach(t => {
            const card = document.createElement('section');
            card.className = 'rt-card dp-tcard is-unmanned';
            card.dataset.truck = t.car_code;
            const h = document.createElement('h3');
            h.className = 'dp-thead-h dp-thead';
            h.innerHTML = '<span class="dp-thead-t"><span class="dp-thead-name"><b></b></span><span class="dp-tstats"></span></span>';
            h.querySelector('b').textContent = truckLabel(t);
            h.querySelector('.dp-tstats').textContent = unmannedText(t);
            card.appendChild(h);
            box.appendChild(card);
        });
        syncFocus();
    }
    const CREW_WARN_HY = { absent: 'Վարորդը չի եկել — նշեք այլ վարորդ («Վարորդ»)',
        twice: 'Այս վարորդը նշված է նաև այլ մեքենայում — վերակազմեք երթերը' };
    // «Դուրս չի գալիս՝ …» — почему отмеченная машина без водителя в день плана (№77)
    function unmannedText(t) {
        const own = driverOf(t.car_code), who = 'վարորդը' + (own ? ' (' + own + ')' : '');
        const where = Object.keys(crewOf().trucks).find(c => crewOf().trucks[c].name === own);
        const there = where ? truckLabel(truckBy(where)) + ' մեքենան' : 'այլ մեքենա';
        if (t.unmanned === 'moved') return 'Դուրս չի գալիս՝ ' + who + ' նստել է ' + there + '․ այդպես օրն ավելի ձեռնտու է։';
        if (t.unmanned === 'busy') return 'Դուրս չի գալիս՝ ' + who + ' այդ օրը վարում է ' + there + '։';
        return 'Դուրս չի գալիս՝ ' + who + ' չի եկել, իսկ ազատ վարորդ չմնաց։';
    }
    // Владелец (№77, ответ 4): машины без водителя в «Վարորդ» — не предупреждение у каждой карточки, а одна строка вверху
    // «N մեքենայի վարորդ նշված չէ» и «Նշել» — диалог «Վարորդ» первой такой машины (сохранили — следующая)
    const noDriver = () => (state.data ? state.data.trucks.filter(t => t.ready && t.selected && !driverOf(t.car_code)) : []);
    function renderNoDriver() {
        const list = noDriver(), box = $('dpNoDriver');
        box.hidden = !list.length || !!state.data.is_past;
        $('dpNoDriverText').textContent = list.length ? list.length + NB + 'մեքենայի վարորդ նշված չէ՝ '
            + list.slice(0, 4).map(truckLabel).join(', ') + (list.length > 4 ? '…' : '') : '';
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
        else if (tr.preloaded) time.insertAdjacentHTML('beforeend', '<small>բեռնված է երեկոյան ·</small>');   // №78
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
        // «Բեռնված է» (№78): логист отмечает по утверждённому плану, снимает — с подтверждением (рейс открепится)
        if (tr.loaded || (state.data.approved && !state.data.is_past)) {
            const ld = document.createElement('button');
            ld.type = 'button';
            ld.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-loadbtn';
            ld.innerHTML = '<i class="fas ' + (tr.loaded ? 'fa-box-open' : 'fa-truck-ramp-box') + '" aria-hidden="true"></i><span></span>';
            ld.lastChild.textContent = tr.loaded ? 'Հանել բեռնված նշումը' : 'Բեռնված է';
            ld.setAttribute('aria-label', ld.lastChild.textContent + '՝ ' + truckLabel(t) + ', երթ ' + (i + 1));
            ld.addEventListener('click', () => {
                if (tr.loaded && !window.confirm('Հանե՞լ «Բեռնված է» նշումը։ Երթն այլևս ամրացված չի լինի բեռնման պատճառով։')) return;
                edit({ action: tr.loaded ? 'unloaded' : 'loaded', trip: tr.id }, tr.loaded ? 'Նշումը հանված է' : 'Նշված է՝ բեռնված է');
            });
            acts.append(ld);
        }
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
        else if (tr.in_reserve) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn" title="Վերադառնում է օրվա վերջի պահուստի ժամին՝ ուշացում չէ">առանց պահուստի</span>');   // №78
        if (tr.over_capacity) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">գերբեռնված</span>');
        if (tr.window_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">ընդունման ժամից դուրս՝ ' + esc(fmt(tr.window_miss)) + '</span>');
        if (tr.center_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">կենտրոն՝ առանց թույլտվության</span>');
        if (tr.vehicle_miss) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">մեքենան չի կարող սպասարկել՝ ' + esc(fmt(tr.vehicle_miss)) + '</span>');
        if (tr.poor) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn">' + esc(fmt(state.data.min_trip_revenue)) + NB + 'դրամից պակաս</span>');
        if (tr.pinned) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-ok"><i class="fas fa-lock" aria-hidden="true"></i>ամրացված</span>');
        if (tr.loaded) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-ok"><i class="fas fa-check" aria-hidden="true"></i>Բեռնված է ժ.' + NB + esc(tr.loaded.at)
            + ((state.data.loaded_by || {})[tr.id] ? ' (' + esc(state.data.loaded_by[tr.id]) + ')' : '') + '</span>');   // №78
        line.append(load, meta, flags);
        head.append(title, time, acts, line);
        div.appendChild(head);
        if (tr.poor && !state.data.is_past) div.appendChild(poorNote(tr));
        if (editing) div.appendChild(tripTools(t, tr, i));
        div.appendChild(stopList(tr.stops, tr.id, editing, tr.lunch));
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
        // №72: рейс уже в пути с взятыми сегодня заказами дня — на завтра нельзя (отвезли бы дважды)
        const moving = new Set(sdTaken().filter(o => o.started).map(o => o.isn));
        if (tr.stops.some(s => (s.orders || []).some(o => moving.has(o.isn)))) { box.append(p); return box; }
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
        // №81: здесь же — насколько план короче или длиннее (в сводке — только экономия)
        const diff = num(b.km) !== null ? Math.round(b.km - plan.summary.km) : 0;
        $('dpBaselineNote').textContent = '≈ ' + fmt(b.km) + NB + 'կմ, ' + fmt(b.liters) + NB + 'լ, ' + pl(b.trips, 'երթ')
            + (diff > 0 ? ' · պլանը ' + fmt(diff) + NB + 'կմ-ով կարճ է' : diff < 0 ? ' · պլանը ' + fmt(-diff) + NB + 'կմ-ով երկար է' : '');
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
        // short — подпись на рабочем экране (только номер машины), trip — кнопка рейса (там её нет: рейс выбирают на шкале)
        const pill = (text, pressed, aria, onClick, color, short, trip) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-lg' + (trip ? ' is-trip' : '');
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
            if (short) {
                const sh = document.createElement('span');
                sh.className = 'n-short';
                sh.textContent = short;
                b.appendChild(sh);
            }
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
                 () => setMapFocus({ truck: t.car_code, trip: null }), color, t.car_code);
            if (!one) t.trips.forEach((tr, i) => pill('երթ ' + (i + 1), !!f && f.trip === tr.id,
                'Միայն ' + truckLabel(t) + ', երթ ' + (i + 1), () => setMapFocus({ truck: t.car_code, trip: tr.id }), color, null, true));
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
        const x = tr.explain, begin = clockMin(tr.loading_start), back = clockMin(tr.return), meal = x.lunch_min || 0;   // обед в рейсе (№61)
        const spare = x.buffer_min || 0;   // запас на рейс (№66)
        const total = begin !== null && back !== null ? back - begin : Math.round(x.loading_min + x.drive_min + x.unload_min + x.wait_min + meal + spare);
        const [loading, drive, unload, wait, lunch, buffer] = roundParts([x.loading_min, x.drive_min, x.unload_min, x.wait_min, meal, spare], total);
        return { total, loading, drive, unload, wait, lunch, buffer };
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
        // большая машина с магазинами Еревана (№68): в расчёте они дороже на плату приоритета малых машин
        const city = num(o.yerevan_km) ? ' Մեծ մեքենա է՝ Երևանի խանութները նրա համար հաշվարկում թանկ են (+' + fmt(o.yerevan_km, 1) + NB + 'կմ)։' : '';
        return truckLabel(o) + ' — կարող էր տանել ≈ ' + fmt(o.liters, 1) + NB + 'լ դիզելով' + diff + '։ '
            + (n ? 'Այս օրն այն արդեն ունի ' + pl(n, 'երթ') + '։' : 'Այս օրն այն երթ չունի։') + city;
    }
    function whyTrip(plan, t, tr, i) {
        const x = tr.explain, day = plan.explain, model = day.model || {}, d = state.data;
        const truck = [truckLabel(t) + (num(t.capacity_kg) === null ? '՝ այս մեքենան այսօր նշված չէ որպես աշխատող։'
            : '՝ տանում է մինչև ' + kgText(t.capacity_kg) + ', ' + (t.center_ok ? 'մտնում է կենտրոն' : 'կենտրոն չի մտնում')
                + (t.big ? ', մեծ մեքենա է' : '') + '։')];
        if (tr.pinned) truck.push('Երթն ամրացված է՝ «Վերակազմել երթերը» սեղմելիս այն չի փոխվի։');
        if (num(x.yerevan_km)) truck.push('Երևանի խանութները մեծ մեքենայով հաշվարկում թանկ են (+' + fmt(x.yerevan_km, 1) + NB
            + 'կմ)՝ փոքր մեքենաներն առաջնահերթ են, բայց ամբողջ օրվա հաշվարկով այս տարբերակը լավագույնն է (կամ փոքրերը չեն հասցնում)։');
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
        if (tt.lunch) bits.push('ճաշ՝ ' + minText(tt.lunch));
        if (tt.buffer) bits.push('ժամանակի պաշար երթի վերջում՝ ' + minText(tt.buffer));
        const time = [capFirst(bits.join(', ')) + '։ Ընդամենը՝ ' + minText(tt.total) + ' (' + tr.loading_start + ' → ' + tr.return + ')։'];
        // обед на складе — своими словами: это не «выезд позже, чтобы не ждать у окна первой точки» (простой — без обеда)
        const depotLunch = tr.lunch && (tr.lunch.where || (tr.lunch.after_stop === null ? 'depot' : 'store')) === 'depot';
        if (depotLunch) time.push('Մինչ բեռնումը վարորդը ճաշում է պահեստում՝ ' + tr.lunch.start + '–' + tr.lunch.end
            + (x.idle_before_min >= 1 ? '․ բացի դրանից, մեքենան սպասում է ' + minText(x.idle_before_min)
                + ', որպեսզի առաջին խանութ հասնի դրա ընդունման ժամի սկզբին' : '') + '։');
        else if (tr.lunch) time.push(lunchText(tr.lunch) + '։');
        // большая машина в зоне Еревана (№68): надбавка — уже в разгрузке
        const city = tr.stops.filter(s => num(s.yerevan_min));
        if (city.length) time.push('Բեռնաթափման մեջ է՝ +' + minText(x.yerevan_min) + '․ մեծ մեքենան Երևանի գոտում '
            + pl(city.length, 'խանութում') + ' ավելի երկար է կանգնում (+' + fmt(city[0].yerevan_min) + NB + 'րոպե յուրաքանչյուրում)։');
        if (x.idle_before_min >= 1 && !depotLunch) time.push((tt.loading ? 'Բեռնումը սկսվում է ' + tr.loading_start : 'Մեքենան մեկնում է ' + tr.depart)
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
        } else if (x.preloaded) {   // №78: вне сезона первый рейс загружен с вечера
            const p = document.createElement('p');
            p.textContent = 'Մեքենան բեռնված է նախորդ երեկոյան՝ առավոտյան բեռնում չկա, մեկնում է ' + tr.depart + '։';
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
        const meal = tr.lunch ? [lunchText(tr.lunch), tr.lunch.start, '', '', '', ''] : null;
        if (meal && tr.lunch.after_stop === null) row(meal);
        tr.stops.forEach((s, k) => {
            row([(k + 1) + '. ' + (s.name || s.code), s.arrive || s.eta, fmt(s.drive_min),
                s.wait_min >= 0.5 ? fmt(s.wait_min) + ' (մինչև ' + s.eta + ')' : '0', fmt(s.unload_min), windowText(s.window) || '—']);
            if (meal && tr.lunch.after_stop === k) row(meal);
        });
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
                + ', բեռնաթափում ' + minText(tt.unload) + (tt.wait ? ', ընդունման ժամի սպասում ' + minText(tt.wait) : '')
                + (tt.lunch ? ', ճաշ ' + minText(tt.lunch) : '') + '։';
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
            if (num(m.learned.lunch) !== null) learned.push('ճաշը ճանապարհին՝ ' + minText(m.learned.lunch));   // обед (№61)
            // запас на рейс и темп машин (№66)
            if (num(m.learned.buffer_pct) !== null) learned.push('ժամանակի պաշար երթի վերջում (երթը ժամանակին է 100-ից ' + fmt(m.learned.buffer_pct) + ' դեպքում)');
            const pace = Object.keys(m.learned.pace || {});
            if (pace.length) learned.push('մեքենայի գործակիցները՝ ' + pace.map(c => truckLabel(truckBy(c)) + ' (բեռնաթափում ×'
                + fmt(m.learned.pace[c][0], 2) + ', ճանապարհ ×' + fmt(m.learned.pace[c][1], 2) + ')').join(', '));
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
                + (x.big ? ', մեծ մեքենա' : '')
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
            // большая машина в Ереване (№68) — только когда правило в деле (сервер: есть большая машина и магазины в зоне)
            // сила приоритета — ползунок настроек (0 — только минуты)
            ['Երևանի գոտի', e.yerevan ? [pl(e.yerevan.stores, 'խանութ') + ' Երևանի գոտում է' + (num(e.yerevan.penalty_km)
                    ? '՝ դրանք նախ տանում են փոքր մեքենաները, մեծը՝ միայն երբ փոքրերին տոննաժ կամ ժամանակ չի հերիքում (մեծ մեքենայի '
                        + 'յուրաքանչյուր այդպիսի կանգառ հաշվարկում արժե որպես +' + fmt(e.yerevan.penalty_km) + NB + 'կմ իր ճանապարհին)։'
                    : '․ փոքր մեքենաների առաջնահերթություն չկա (կարգավորումներում՝ «միայն րոպեներ»)։'),
                num(e.yerevan.minutes) ? 'Մեծ մեքենայի կանգառին Երևանի գոտում ավելանում է ' + minText(e.yerevan.minutes) + '։' : ''] : []],
            ['Ընդունման ժամեր', [e.window_stores ? pl(e.window_stores, 'խանութ') + ' ունի ընդունման ժամ՝ երթերը կազմվում են այնպես, որ հասնենք ժամանակին, իսկ վաղ հասնելու դեպքում մեքենան սպասում է։'
                : 'Այս օրվա խանութներից ոչ մեկն ընդունման ժամ չունի։']],
            ['Մեքենաների սահմանափակումներ', [e.access_stores ? pl(e.access_stores, 'խանութ') + ' ունի մեքենաների սահմանափակում՝ դրանք տանում են միայն թույլատրված մեքենաները։' : '']],
            // №78, ответы 18 и 20: машина отдельного рейса — снята ли лишняя машина и на сколько вырос ծախս дня
            ['Առանձին երթ', e.solo_spare ? [e.solo_spare.truck
                ? 'Առանձին երթի մեքենան վերադառնում է և տանում է նաև սովորական խանութներ․ ' + truckLabel(truckBy(e.solo_spare.truck))
                    + '-ն այսօր պետք չէ (օրվա դիզելը և մաշվածքը' + (num(e.solo_spare.delta_pct) > 0 ? ' աճում են ' + fmt(e.solo_spare.delta_pct, 1) + '%-ով' : ' չեն աճում')
                    + ', թույլատրված է մինչև ' + fmt(e.solo_spare.limit_pct) + '%)։'
                : num(e.solo_spare.delta_pct) !== null
                    ? 'Առանձին երթի մեքենան տանում է միայն իր խանութը՝ առանց լրացուցիչ մեքենայի օրվա դիզելը և մաշվածքը կաճեին ' + fmt(e.solo_spare.delta_pct, 1)
                        + '%-ով (թույլատրված է մինչև ' + fmt(e.solo_spare.limit_pct) + '%)։'
                    : 'Առանձին երթի մեքենան տանում է միայն իր խանութը՝ առանց լրացուցիչ մեքենայի բոլոր խանութները չեն տեղավորվում։'] : []],
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

    // №86: нажали магазин на карте (рабочий экран) — карточка его машины справа, строка магазина подсвечена. Та же машина
    // (или тот же рейс) уже выбрана — карта не перерисовывается и не меняет масштаб, только строка
    const MAP_HINT = '<br><small class="dp-tip-hint">Սեղմեք՝ քարտը, քաշեք՝ այլ երթի վրա</small>';
    function pickFromMap(t, tr, s) {
        if (state.mapDragged) return;   // конец перетаскивания — не нажатие
        const f = state.mapFocus;
        if (!(f && f.truck === t.car_code && (f.trip == null || f.trip === tr.id) && state.open.has(t.car_code))) focusFromBoard(t, tr);
        flashStop(tr.id, s.customer_id);
    }
    function flashStop(tripId, cid) {
        const row = $('dpTruckCards').querySelector('.dp-trip[data-trip="' + tripId + '"] .dp-stop[data-cid="' + cid + '"]');
        if (!row) return;
        const side = $('dpWsSide');
        if (side.contains(row)) side.scrollTo({ top: row.getBoundingClientRect().top - side.getBoundingClientRect().top + side.scrollTop - side.clientHeight / 3,
                                                behavior: calm() ? 'auto' : 'smooth' });
        clearTimeout(row.dpFlash);
        row.classList.remove('is-flash');
        void row.offsetWidth;   // повторное нажатие — подсветка заново
        row.classList.add('is-flash');
        row.dpFlash = setTimeout(() => row.classList.remove('is-flash'), 1800);
    }
    // наведение на кружок шкалы или строку магазина в карточке — точка на карте обведена, подпись открыта
    function hoverStop(tripId, cid, on) {
        const map = state.map, m = state.mapMarks && state.mapMarks.get(tripId + ':' + cid);
        if (state.hoverRing) { state.hoverRing.remove(); state.hoverRing = null; }
        if (!map || !m || !map.hasLayer(m)) return;
        if (!on) { m.closeTooltip(); return; }
        state.hoverRing = L.circleMarker(m.getLatLng(), { radius: 17, color: '#fff', weight: 3, fill: false, interactive: false }).addTo(map);
        m.openTooltip();
    }
    function initStopHover() {
        const root = $('dpBody');
        const key = (el) => {
            if (!el || !el.closest) return null;
            const tick = el.closest('i.dp-tick[data-cid]');
            const bar = tick && tick.closest('.dp-bar[data-trip]');
            if (bar) return bar.dataset.trip + ':' + tick.dataset.cid;
            const row = el.closest('.dp-stop[data-cid]');
            const trip = row && row.closest('.dp-trip[data-trip]');
            return trip ? trip.dataset.trip + ':' + row.dataset.cid : null;
        };
        let cur = null;
        const set = (k) => {
            if (k === cur) return;
            if (cur) hoverStop(...cur.split(':'), false);
            cur = k;
            if (k) hoverStop(...k.split(':'), true);
        };
        root.addEventListener('mouseover', (e) => { if (!state.dragStop) set(key(e.target)); });
        root.addEventListener('mouseleave', () => set(null));
    }
    // №86: перенос магазина на карте (как в Routific): тянут точку — видны все рейсы дня, ближайшая линия другого рейса
    // (до DROP_PX пикселей, у машины, которой магазин разрешён) выделена, подпись точки называет её; отпустили — правка
    // move, как перенос на шкалу; мимо — точка возвращается на место
    const DROP_PX = 28;
    function mapDraggable(m, t, tr, s, tip) {
        let drag = null;
        // конец переноса: отпустили (dragend) или карту перерисовали посреди него (cancel из drawMap)
        const finish = () => {
            if (!drag) return null;
            const hit = drag.hit, orig = drag.orig;
            if (drag.raf) cancelAnimationFrame(drag.raf);
            if (hit) hit.line.setStyle(hit.style);
            drag.extra.remove();
            drag = null;
            state.mapDrag = null;
            $('rtDispatch').classList.remove('is-mapdrag');
            setTimeout(() => { state.mapDragged = false; }, 0);   // click сразу после переноса (если придёт) — не нажатие
            const el = m.getTooltip().getElement();
            if (el) el.classList.remove('dp-dragtip');
            m.setTooltipContent(tip + MAP_HINT);
            m.setLatLng(orig);   // новая карта придёт с ответом сервера; мимо рейса — точка на своём месте
            return hit;
        };
        m.on('dragstart', () => {
            const map = state.map, plan = state.data.plan, depot = state.data.depot ? [state.data.depot.lat, state.data.depot.lon] : null;
            const extra = L.layerGroup().addTo(map);
            const targets = [];
            if (!state.busy) plan.trucks.forEach(tt => tt.trips.forEach((x, xi) => {
                if (x.id === tr.id || !vehicleAllowed(s, tt.car_code)) return;
                let line = state.mapLines.get(x.id);
                if (!line) {   // рейс сейчас скрыт (выбрана одна машина) — на время переноса бледной линией
                    const pts = x.stops.filter(q => q.lat !== null).map(q => [q.lat, q.lon]);
                    const raw = depot ? [depot, ...pts, depot] : pts;
                    if (raw.length < 2) return;
                    const road = state.roadCache.get(raw.map(q => q[0].toFixed(5) + ',' + q[1].toFixed(5)).join(';'));
                    line = L.polyline(Array.isArray(road) && road.length > 1 ? road : raw,
                        { color: truckColor(tt.car_code), weight: 3, opacity: .55, dashArray: '4 6', interactive: false }).addTo(extra);
                }
                targets.push({ t: tt, tr: x, n: xi + 1, line, style: { weight: line.options.weight, opacity: line.options.opacity } });
            }));
            drag = { orig: m.getLatLng(), extra, targets, hit: null, raf: 0 };
            state.mapDrag = { cancel: finish };
            state.mapDragged = true;
            m.closeTooltip();
            if (state.busy) {
                m.setTooltipContent('Սպասեք՝ նախորդ փոփոխությունը դեռ պահպանվում է').openTooltip();
                const el = m.getTooltip().getElement();
                if (el) el.classList.add('dp-dragtip');
            }
            $('rtDispatch').classList.add('is-mapdrag');
        });
        m.on('drag', (e) => {
            if (!drag || drag.raf) return;
            const ll = e.latlng;
            drag.raf = requestAnimationFrame(() => {
                if (!drag) return;
                drag.raf = 0;
                const map = state.map, p = map.latLngToContainerPoint(ll);
                let best = null, bd = DROP_PX;
                drag.targets.forEach(g => {   // по текущему экрану: линия могла смениться на дорогу, карту — сдвинуть колесом
                    const px = g.line.getLatLngs().map(q => map.latLngToContainerPoint(q));
                    for (let i = 1; i < px.length; i++) {
                        const dd = L.LineUtil.pointToSegmentDistance(p, px[i - 1], px[i]);
                        if (dd < bd) { bd = dd; best = g; }
                    }
                });
                if (best === drag.hit) return;
                if (drag.hit) drag.hit.line.setStyle(drag.hit.style);
                drag.hit = best;
                if (best) {
                    best.line.setStyle({ weight: 7, opacity: 1 });
                    best.line.bringToFront();
                    m.setTooltipContent(esc(s.name || s.code) + '<br>→ ' + esc(truckLabel(best.t)) + ', երթ ' + best.n).openTooltip();
                    const el = m.getTooltip().getElement();
                    if (el) el.classList.add('dp-dragtip');   // подписи точек под курсором на время переноса скрыты
                } else m.closeTooltip();
            });
        });
        m.on('dragend', () => {
            const hit = finish();
            if (hit) edit({ action: 'move', customer_id: s.customer_id, from_trip: tr.id, to_trip: hit.tr.id, truck: null },
                '«' + (s.name || s.code) + '» տեղափոխվեց՝ ' + truckLabel(hit.t) + ', երթ ' + hit.n);
        });
    }

    function drawMap() {
        ensureMap();
        if (!state.map) return;
        const d = state.data, plan = d.plan;
        if (state.mapDrag) state.mapDrag.cancel();
        if (state.hoverRing) state.hoverRing.remove();
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
        // №86: на рабочем экране точка — ссылка на строку в карточке машины, и её можно перетащить на другой рейс
        const ws = wsOn();   // карта рисуется до layoutWs — класс is-ws может ещё не стоять
        state.mapWs = ws;
        const canDrag = ws && !d.is_past && finePointer();
        state.mapMarks = new Map();   // «рейс:магазин» → маркер (подсветка со шкалы и из карточки)
        state.mapLines = new Map();   // рейс → его линия на карте (цели переноса)
        state.hoverRing = null;
        plan.trucks.forEach(t => {
            const color = truckColor(t.car_code);
            t.trips.forEach((tr, ti) => {
                if (!shown(t, tr)) return;
                const pts = tr.stops.filter(s => s.lat !== null).map(s => [s.lat, s.lon]);
                const line = depot ? [depot, ...pts, depot] : pts;
                if (line.length > 1) {
                    const pl = L.polyline(line, { color, weight: state.mapFocus ? 4 : 3, opacity: .85, dashArray: ti % 2 ? '6 6' : null }).addTo(state.layers);
                    routes.push([pl, line]);
                    state.mapLines.set(tr.id, pl);
                }
                tr.stops.forEach((s, si) => {
                    if (s.lat === null) return;
                    bounds.push([s.lat, s.lon]);
                    // одна машина или рейс — точки с номерами по порядку объезда (у машины с несколькими рейсами — «рейс.точка»)
                    const label = state.mapFocus ? (state.mapFocus.trip == null && t.trips.length > 1 ? (ti + 1) + '.' : '') + (si + 1) : null;
                    const m = label !== null
                        ? L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'dp-npin', html: '<span' + (String(label).length > 3 ? ' class="is-long"' : '') + ' data-trip="' + tr.id + '" style="background:' + color + '">' + label + '</span>', iconSize: [26, 26] }), keyboard: false, draggable: canDrag })
                        : ws   // перетащить можно только маркер, не кружок на canvas
                            ? L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'dp-dpin', html: '<span data-trip="' + tr.id + '" style="background:' + color + '"></span>', iconSize: [16, 16] }), keyboard: false, draggable: canDrag })
                            : L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: color, fillOpacity: 1 });
                    const tip = esc(truckLabel(t)) + ' · երթ ' + (ti + 1) + ' · №' + (si + 1) + '<br>' + esc(s.name || s.code);
                    m.bindTooltip(tip + (canDrag ? MAP_HINT : ''));
                    state.mapMarks.set(tr.id + ':' + s.customer_id, m);
                    if (ws) {
                        m.on('click', () => pickFromMap(t, tr, s));
                        if (canDrag) mapDraggable(m, t, tr, s, tip);
                    } else {
                        const when = (s.eta ? 'ժամանում ≈ ' + s.eta : '') + (windowText(s.window) ? ' · ընդունման ժամ՝ ' + windowText(s.window) : '');
                        m.bindPopup('<div class="rt-pop"><b>' + esc(s.name || s.code) + '</b><br>' + esc(s.address || '') + '<br>'
                            + esc(kgText(s.kg)) + ' · ' + esc(money(s.revenue)) + '<br>' + esc(truckLabel(t)) + ', երթ ' + (ti + 1) + ', կետ ' + (si + 1)
                            + (when ? '<br>' + esc(when) : '') + '</div>');
                    }
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
            L.marker(depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<span><i class="fas fa-warehouse" aria-hidden="true"></i></span>', iconSize: [28, 28], iconAnchor: [14, 14] }), keyboard: false, zIndexOffset: 1000 })
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
        if (state.map && all.length && !state.mapUserMoved && !state.mapDrag) fitMap(state.map, all);
    }

    // ---------- Действия ----------
    function setBusy(on) {
        state.busy = on;
        document.querySelectorAll('#dpBody button, #dpBody select, #dpAgentsList input, #dpSendState button, #dpWsActs button').forEach(x => {
            if (on) { x.dataset.wasDisabled = x.disabled ? '1' : ''; x.disabled = true; } else if (x.dataset.wasDisabled !== undefined) { x.disabled = x.dataset.wasDisabled === '1'; delete x.dataset.wasDisabled; }
        });
        $('rtDispatch').setAttribute('aria-busy', String(on));
    }

    async function build() {
        if (state.busy) return;
        if (state.data.approved) { showActionError(new Error(APPROVED_HY)); return; }   // №73: сначала снять утверждение
        const trucks = selectedTrucks();
        if (!trucks.length) { showActionError(new Error('Նշեք գոնե մեկ մեքենա 1-ին քայլում։')); return; }
        if (agentsNoneLeft()) { showActionError(new Error('Նշեք գոնե մեկ մենեջեր «Որ մենեջերների պատվերներն ենք տանում» ցուցակում։')); return; }
        // сегодня после начала дня машин (№72): пересборка раскладывает заново и рейсы, которые уже грузятся или в пути
        const clock = new Date();
        const sdNow = sdData() && sdData().today ? sdData().now
            : state.data.day === state.data.today ? String(clock.getHours()).padStart(2, '0') + ':' + String(clock.getMinutes()).padStart(2, '0') : null;
        if (state.data.plan && sdNow && sdNow >= state.data.work_start
            && !window.confirm('Ժամը ' + sdNow + ' է։ Վերակազմելիս ծրագիրը նորից կբաշխի նաև այն երթերը, որոնք արդեն բեռնվում են կամ ճանապարհին են '
                + '(ամրացված երթերը կմնան)։ Շարունակե՞լ։')) return;
        hideActionError();
        setBusy(true);
        $('dpBuildText').textContent = 'Կազմում եմ երթերը…';
        try {
            // фильтр — только свой (до сборки или изменённый тут): иначе сервер берёт фильтр плана, а не копию этой вкладки
            const body = { date: state.day, trucks };
            if (!state.data.plan || agentsDirty()) body.agents_off = [...agentsOff()];
            let data;
            try { data = await api('POST', '/api/routes/dispatch/build', body); } catch (e) {
                // №78: новый фильтр менеджеров снял бы точки загруженного рейса — спросить и собрать с подтверждением
                if (!(e.data && e.data.loaded_confirm === true && window.confirm(e.message))) throw e;
                data = await api('POST', '/api/routes/dispatch/build', { ...body, confirm_loaded: true });
            }
            state.geoChanged = null;
            state.agentsPick = null;
            setBusy(false);
            setData(data);
            toast('Երթերը կազմված են՝ ' + pl(data.plan.summary.trips, 'երթ') + ', ≈ ' + fmt(data.plan.summary.km) + NB + 'կմ');
            if (!$('dpDrawer').hidden) { $('dpDrawer').hidden = true; $('dpPrepOpen').setAttribute('aria-expanded', 'false'); renderSteps(); }
            if ($('rtDispatch').classList.contains('is-ws')) { window.scrollTo(0, 0); $('dpBoard').querySelector('.dp-blabel')?.focus({ preventScroll: true }); }
            else $('dpStep3Title').focus();
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
            // №78: правка загруженного рейса — товар уже в машине: спросить и повторить с подтверждением
            if (e.data && e.data.loaded_confirm === true && !body.confirm_loaded && window.confirm(e.message)) {
                return edit({ ...body, confirm_loaded: true }, okText);
            }
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
            state.agentsPick = null;
            setBusy(false);
            setData(data);
            toast('Օրվա պլանը ջնջված է — կարելի է նորից կազմել երթերը։');
        } catch (e) { setBusy(false); render(); showActionError(e); }
    }

    // ---------- Ход дня на шкале (№82, как мониторинг Яндекса / Routific live) ----------
    // Сегодня, пока открыта страница с рейсами, — раз в PROGRESS_MS факт терминалов «Առաքիչ»; кружок магазина красится,
    // у машины — «✓ доставлено / всего». Прошлый и будущий день, «Առաքիչ» нет — кружки как в плане
    const PROGRESS_MS = 60 * 1000;
    const PG_HY = { done: 'առաքված', partial: 'մասնակի', refused: 'հրաժարվել է', here: 'մեքենան տեղում է', late: 'ուշանում է', pending: 'սպասում է' };
    let progressSeq = 0;
    async function loadProgress() {
        const d = state.data;
        if (!d || !d.plan || d.day !== d.today || document.hidden || !$('rtDispatch').classList.contains('is-ws')) return;
        const day = d.day, seq = ++progressSeq;
        let r;
        try { r = await api('GET', '/api/routes/dispatch/progress?date=' + encodeURIComponent(day)); } catch (e) { return; }
        if (seq !== progressSeq || !state.data || state.data.day !== day) return;   // пришёл более свежий ответ / сменили день
        state.progress = r && r.live ? { day, trucks: r.trucks || {}, now: r.now } : null;
        paintProgress();
    }
    function paintProgress() {
        const p = state.progress && state.data && state.progress.day === state.data.day ? state.progress : null;
        $('dpBoard').querySelectorAll('.dp-bar').forEach(bar => {
            const car = p ? p.trucks[bar.dataset.truck] : null;
            bar.querySelectorAll('i.dp-tick').forEach(tick => {
                const g = car ? car[tick.dataset.cid] : null;
                tick.classList.remove('pg-done', 'pg-partial', 'pg-refused', 'pg-here', 'pg-late');
                if (tick.dataset.baseTitle === undefined) tick.dataset.baseTitle = tick.title;
                tick.title = tick.dataset.baseTitle;
                if (!g || g.s === 'pending') return;
                tick.classList.add('pg-' + g.s);
                tick.title += ' — ' + PG_HY[g.s] + (g.at ? ' ' + g.at : '') + (g.s === 'late' && g.delay ? ' (+' + g.delay + ' ր)' : '');
            });
        });
        $('dpBoard').querySelectorAll('.dp-blabel').forEach(lab => {
            const el = lab.querySelector('.dp-blx-pg');
            const car = p ? p.trucks[lab.dataset.truck] : null;
            if (!el) return;
            el.hidden = !car;
            if (!car) return;
            const all = Object.values(car), done = all.filter(g => g.s === 'done' || g.s === 'partial').length;
            const refused = all.filter(g => g.s === 'refused').length, late = all.filter(g => g.s === 'late').length;
            el.textContent = '✓ ' + done + '/' + all.length + (refused ? ' · ✗ ' + refused : '') + (late ? ' · ուշ ' + late : '');
            el.classList.toggle('is-late', late > 0);
            if (lab.dataset.baseLabel === undefined) lab.dataset.baseLabel = lab.getAttribute('aria-label') || '';
            lab.setAttribute('aria-label', lab.dataset.baseLabel + ' · առաքված ' + done + '/' + all.length
                + (refused ? ', հրաժարում ' + refused : '') + (late ? ', ուշանում է ' + late : ''));
        });
        $('dpBoard').classList.toggle('has-progress', !!p);
    }

    // ---------- Вариант А (ответ владельца №82): рабочий экран на широком экране ----------
    // Узлы страницы переносятся в слоты рабочего экрана и обратно (место запоминает комментарий-«якорь»): id, обработчики и
    // состояние (карта Leaflet, раскрытые карточки) те же. Ширина меньше 1101 px или рейсов нет — прежняя страница
    const WIDE = window.matchMedia ? window.matchMedia('(min-width: 1101px)') : null;
    // вид (как «Timeline / List» у Routific): рабочий экран (по умолчанию) или прежний список — помнится в этом браузере
    let wsView = 'ws';
    try { wsView = window.localStorage.getItem('dpLayout') === 'list' ? 'list' : 'ws'; } catch (e) { /* хранилище недоступно */ }
    const wsOn = () => !!(wsView === 'ws' && WIDE && WIDE.matches && state.data && state.data.plan && state.data.plan.trucks.length);
    function setView(v) {
        wsView = v;
        try { window.localStorage.setItem('dpLayout', v); } catch (e) { /* только на эту страницу */ }
        layoutWs();
        renderInbox();   // счётчик «без водителя» — только на рабочем экране
        window.scrollTo(0, 0);
    }
    const wsHome = new Map();
    function wsPark(node, slot) {
        if (!node || !slot) return;
        if (!wsHome.has(node)) { const c = document.createComment('dp-home'); node.parentNode.insertBefore(c, node); wsHome.set(node, c); }
        if (node.parentNode !== slot) slot.appendChild(node);
    }
    function wsUnpark(node) {
        const c = node && wsHome.get(node);
        if (c && c.parentNode && node.previousSibling !== c) c.parentNode.insertBefore(node, c.nextSibling);
    }
    const wsNodes = () => [[$('dpApprove'), 'dpWsActs'], [document.querySelector('#rtDispatch .dp-summary-cta'), 'dpWsActs'],
        [$('dpPlanStats'), 'dpWsKpi'], [$('dpMapBox'), 'dpWsMap'], [$('dpBoard'), 'dpWsBottom'], [$('dpTruckCards'), 'dpWsSide'],
        [$('dpWhy'), 'dpWsSide'], [document.querySelector('#rtDispatch .dp-prep'), 'dpDrawerBody'], [$('dpRun'), 'dpDrawerBody']];
    function layoutWs() {
        const on = wsOn(), root = $('rtDispatch'), was = root.classList.contains('is-ws');
        if (on) wsNodes().forEach(([n, slot]) => wsPark(n, $(slot)));
        else if (was) wsNodes().forEach(([n]) => wsUnpark(n));
        root.classList.toggle('is-ws', on);
        document.body.classList.toggle('dp-ws-on', on);
        $('dpWs').hidden = !on;
        $('dpWsActs').hidden = !on;
        if (!on && !$('dpDrawer').hidden) { $('dpDrawer').hidden = true; $('dpPrepOpen').setAttribute('aria-expanded', 'false'); renderSteps(); }
        $('dpWsSide').hidden = !on || !state.mapFocus;
        if (on && !$('dpMapBox').open) $('dpMapBox').open = true;
        const fab = $('dpAiOpen');
        $('dpWsAi').hidden = !fab || (fab.hidden && $('dpAi').hidden);
        if (on) sizeWs();
        if (on !== was && state.map) setTimeout(() => {
            state.map.invalidateSize({ pan: false });
            // №86: точки на рабочем экране другие (нажатие ведёт в карточку, перенос) — карта нарисована для другого вида
            if (state.data && state.data.plan && state.mapWs !== on) drawMap();
            else if (state.mapBounds && !state.mapUserMoved) fitMap(state.map, state.mapBounds);
        }, 0);
        // M3: подписи Яндекса / Leaflet справа внизу — не под карточкой машины
        $('dpWs').classList.toggle('has-side', on && !!state.mapFocus);
    }
    // высота рабочего экрана — до низа окна от его верха (шапка дашборда, день, подсказка и счётчики — выше)
    function sizeWs() {
        const ws = $('dpWs');
        if (ws.hidden) return;
        const top = ws.getBoundingClientRect().top + window.scrollY;
        $('rtDispatch').style.setProperty('--ws-h', Math.max(440, Math.round(window.innerHeight - top - 12)) + 'px');
    }
    function openDrawer(open) {
        $('dpDrawer').hidden = !open;
        $('dpPrepOpen').setAttribute('aria-expanded', String(open));
        renderSteps();   // в панели шаги открыты (renderSteps смотрит на панель), закрыли — снова свёрнуты в строку
        if (open) {
            if ($('dpNoCoords').open) ensurePickMap();
            if ($('dpGeoSug').open) renderGeoSug();
            $('dpDrawerClose').focus();
        } else if (!$('dpPrepOpen').closest('[hidden]')) $('dpPrepOpen').focus();
    }
    function initWs() {
        $('dpPrepOpen').addEventListener('click', () => openDrawer($('dpDrawer').hidden));
        $('dpDrawerClose').addEventListener('click', () => openDrawer(false));
        $('dpDrawer').addEventListener('keydown', (e) => { if (e.key === 'Escape') openDrawer(false); });
        const closeSide = () => {
            const code = state.mapFocus && state.mapFocus.truck;
            if (state.data && state.data.plan) setMapFocus(null);
            const lab = code && [...$('dpBoard').querySelectorAll('.dp-blabel')].find(b => b.dataset.truck === code);
            if (lab) lab.focus({ preventScroll: true });
        };
        $('dpWsClose').addEventListener('click', closeSide);
        $('dpWsSide').addEventListener('keydown', (e) => { if (e.key === 'Escape' && !e.target.closest('select')) closeSide(); });
        $('dpWsAi').addEventListener('click', () => { const fab = $('dpAiOpen'); if (fab) fab.click(); });
        $('dpViewList').addEventListener('click', () => setView('list'));
        $('dpViewWs').addEventListener('click', () => setView('ws'));
        if (WIDE) {
            const sync = () => { if (state.data) { layoutWs(); renderInbox(); } };
            if (WIDE.addEventListener) WIDE.addEventListener('change', sync); else if (WIDE.addListener) WIDE.addListener(sync);
        }
        window.addEventListener('resize', () => { if ($('rtDispatch').classList.contains('is-ws')) sizeWs(); });
        // шапка, подсказка и счётчики меняют высоту после отрисовки (шрифты, переносы) — высота рабочего экрана следом
        if (typeof window.ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(() => { if ($('rtDispatch').classList.contains('is-ws')) sizeWs(); });
            [document.querySelector('#rtDispatch .dp-mast'), $('dpTodo'), $('dpInbox'), $('dpSameDay'), $('dpActionError')].forEach(el => el && ro.observe(el));
        }
    }

    // ---------- Вкладки на телефоне (№81, как Sidebar / Map у Onfleet) ----------
    const PHONE = window.matchMedia ? window.matchMedia('(max-width: 767px)') : null;
    const tabsOn = () => !!(PHONE && PHONE.matches && state.data && state.data.plan);
    function renderTabs() {
        const on = tabsOn(), root = $('rtDispatch');
        $('dpTabs').hidden = !on;
        if (!on) { delete root.dataset.tab; delete document.body.dataset.dpTab; return; }
        root.dataset.tab = state.tab;
        document.body.dataset.dpTab = state.tab;
        $('dpTabs').querySelectorAll('.dp-tab').forEach(b => { if (b.dataset.tab !== 'ai') b.setAttribute('aria-pressed', String(b.dataset.tab === state.tab)); });
        const ai = $('dpTabAi'), fab = $('dpAiOpen');
        if (ai) ai.hidden = !fab || (fab.hidden && $('dpAi').hidden);
    }
    function setTab(tab) {
        state.tab = tab;
        renderTabs();
        window.scrollTo(0, 0);
        // карта, нарисованная во скрытой вкладке, сама впишет точки (watchSize); раскрыть её блок, если свёрнут
        if (tab === 'map' && !$('dpMapBox').open) $('dpMapBox').open = true;
    }
    function initTabs() {
        $('dpTabs').addEventListener('click', (e) => {
            const b = e.target.closest('.dp-tab');
            if (!b) return;
            if (b.dataset.tab === 'ai') { const fab = $('dpAiOpen'); if (fab) fab.click(); return; }
            setTab(b.dataset.tab);
        });
        if (PHONE) {
            const sync = () => { if (state.data) renderTabs(); };
            if (PHONE.addEventListener) PHONE.addEventListener('change', sync); else if (PHONE.addListener) PHONE.addListener(sync);
        }
        // кнопка AI (routes_dispatch_ai.js) появляется и прячется сама — вкладка AI вслед за ней
        const fab = $('dpAiOpen');
        if (fab && typeof window.MutationObserver !== 'undefined') {
            const mo = new MutationObserver(() => { if (state.data) renderTabs(); });
            mo.observe(fab, { attributes: true, attributeFilter: ['hidden'] });
            mo.observe($('dpAi'), { attributes: true, attributeFilter: ['hidden'] });
        }
    }

    // ---------- Печать и Excel ----------
    // №81: бумага (лист водителей, Excel, «Բեռնագիր») должна совпадать с терминалом водителя. Есть неотправленные правки
    // (у машины code или в отборе заказов) — сначала окно «Ուղարկել և շարունակել»: отправка, затем сама бумага. Окно —
    // на странице, не confirm(): клик по его кнопке — действие пользователя, после него браузер ещё даст открыть печать
    function unsentFor(code) {
        const d = state.data, u = d.sent && !d.is_past ? d.unsent : null;
        return !!u && (!code || u.trucks.includes(code) || u.orders);
    }
    let paperNext = null;
    function paper(code, go) {
        if (state.busy) return;
        if (!unsentFor(code)) { go(false); return; }
        paperNext = go;
        $('dpSfLead').textContent = 'Վարորդների «Առաքիչ» ծրագրում դեռ ' + builtWhen(state.data.sent.at) + '-ի պլանն է։ '
            + unsentDetail(state.data.unsent) + ' Որպեսզի թուղթը համընկնի վարորդի ծրագրի հետ, նախ ուղարկեք փոփոխությունները։';
        $('dpSendFirstDlg').showModal();
    }
    const sendForPaper = async () => !!(await edit({ action: 'send' }, SENT_OK));
    const POPUP_HY = 'Զննարկիչը թույլ չտվեց բացել տպման պատուհանը — թույլատրեք թռուցիկ պատուհանները այս կայքի համար։';
    function printSheets() { paper(null, printSheetsGo); }
    async function printSheetsGo(send) {
        if (!state.data.plan) return;
        // окно — сразу по нажатию: открытое после ответа сервера браузер счёл бы всплывающим
        const w = window.open('', '_blank');
        if (!w) { showActionError(new Error(POPUP_HY)); return; }
        if (send) {
            w.document.write('<!doctype html><html lang="hy"><head><meta charset="utf-8"><title>Առաքում</title></head>'
                + '<body style="font-family:Segoe UI,Sylfaen,Arial,sans-serif;padding:24px">Ուղարկում եմ փոփոխությունները…</body></html>');
            w.document.close();
            if (!(await sendForPaper())) { try { w.close(); } catch (e) { /* уже закрыто */ } return; }
            if (w.closed) return;
        }
        printSheetsTo(w);
    }
    function printSheetsTo(w) {
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
        w.document.open();
        w.document.write(html);
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* окно закрыли раньше */ } }, 300);
    }

    function exportExcel() { paper(null, exportExcelGo); }
    async function exportExcelGo(send) {
        if (send && !(await sendForPaper())) return;
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

    // ---------- Բեռնագիր (ответ владельца №57) ----------
    // Что машина грузит на складе в каждом рейсе: GET /api/routes/dispatch/waybill (товары — из проведённых накладных ERP,
    // где они уже есть, иначе из заказов; тяжёлый магазин на несколько рейсов делится целыми упаковками). Печать — окно с
    // листом на рейс, Excel — файл машины, лист на рейс. Ответ сверяется с планом на экране (rev и состав каждого рейса):
    // разошлись — ошибка «обновите страницу», а не накладная по другому плану.
    function waybillActs(t) {
        const box = document.createElement('div');
        box.className = 'dp-tacts';
        const mk = (ico, text, label, fn) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-wbbtn';
            b.innerHTML = '<i class="fas ' + ico + '" aria-hidden="true"></i><span></span>';
            b.lastChild.textContent = text;
            b.setAttribute('aria-label', label + truckLabel(t));
            b.title = label + truckLabel(t);       // на средних экранах у кнопки только значок
            b.addEventListener('click', () => fn(t.car_code, b));
            return b;
        };
        const drv = mk('fa-user-pen', 'Վարորդ', 'Վարորդ և առաքիչ՝ ', openDriver);
        drv.classList.add('dp-drvbtn');
        box.append(drv, mk('fa-print', 'Բեռնագիր', 'Տպել բեռնագիրը՝ ', (c, b) => paper(c, (send) => printWaybill(c, b, send))),
            mk('fa-file-excel', 'Excel', 'Բեռնագիրը Excel-ով՝ ', (c, b) => paper(c, (send) => excelWaybill(c, b, send))));
        return box;
    }

    // ---------- Водитель и առաքիչ машины (ответы владельца №62) ----------
    // Закреплены за машиной, но меняются часто: выбор действует с дня на странице и до следующей смены или только этот
    // день (подмена); прежние дни не меняются. Двое в машине — «Վարորդ» и необязательный «Առաքիչ», правила те же.
    // «1 հոկտեմբերից» — с какого дня (dayHuman даёт родительный падеж «1 հոկտեմբերի»)
    const dayFrom = (s) => dayHuman(s).replace(/ի$/, 'ից');
    const CREW = {   // роль → ключи ответа дня, поля диалога, поле запроса
        driver: { names: 'drivers', subs: 'substitutes', pick: 'dpDriverPick', box: 'dpDriverNewBox', input: 'dpDriverName', field: 'name',
            dayBox: 'dpDriverOnlyDay', dayField: 'only_day',
            word: 'վարորդ', newText: '+ Նոր վարորդ (ERP-ում չկա)…', needName: 'Գրեք նոր վարորդի անունը' },
        helper: { names: 'helpers', subs: 'helper_substitutes', pick: 'dpHelperPick', box: 'dpHelperNewBox', input: 'dpHelperName', field: 'helper',
            dayBox: 'dpHelperOnlyDay', dayField: 'helper_only_day',
            word: 'առաքիչ', newText: '+ Նոր առաքիչ (ERP-ում չկա)…', needName: 'Գրեք նոր առաքիչի անունը' },
    };
    const ROLES = Object.keys(CREW);
    const isSub = (code, role = 'driver') => !!(state.data && Array.isArray(state.data[CREW[role].subs]) && state.data[CREW[role].subs].includes(code));
    const driverOf = (code, role = 'driver') => {
        const m = state.data && state.data[CREW[role].names];
        return isObj(m) && typeof m[code] === 'string' ? m[code] : '';
    };
    const crewOf = () => (state.data && isObj(state.data.crew) && isObj(state.data.crew.trucks) ? state.data.crew
        : { drivers: [], trucks: {}, stale: false });
    const crewTruck = (code) => (isObj(crewOf().trucks[code]) ? crewOf().trucks[code] : null);
    // «վարորդ՝ Արամ · առաքիչ՝ Կարեն (փոխարինող)» — шапка карточки и уведомление
    const crewText = (code) => ROLES.filter(r => driverOf(code, r))
        .map(r => CREW[r].word + '՝ ' + driverOf(code, r) + (isSub(code, r) ? ' (փոխարինող)' : '')).join(' · ');
    function openDriver(code) {
        if (state.busy) return;
        const t = wbTruck(state.data, code) || state.data.trucks.find(x => x.car_code === code);
        if (!t) return;
        state.driverCar = code;
        $('dpDriverLead').textContent = truckLabel(t);
        // у каждого свой срок: «только этот день» (подмена) — по умолчанию, если у роли уже кто-то есть (меняют — скорее
        // подменяют), без отметки — с этого дня до следующей смены; прошедший день — только он (отметок нет, как и сервер)
        const past = !!state.data.is_past;
        ROLES.forEach(r => {
            const c = CREW[r];
            fillDriverPick(r, driverOf(code, r));
            $(c.input).value = '';
            $(c.dayBox).closest('label').hidden = past;
            $(c.dayBox).closest('label').querySelector('.dp-crew-day-t').textContent = 'Միայն ' + dayHuman(state.day) + 'ն (փոխարինում)';
            $(c.dayBox).checked = !!(driverOf(code, r) || isSub(code, r));
        });
        $('dpDriverHint').textContent = past
            ? 'Միայն ' + dayHuman(state.day) + ' համար՝ օրն արդեն անցել է․ մյուս օրերը չեն փոխվում։'
            : 'Առանց նշման՝ ' + dayFrom(state.day) + ' սկսած՝ մինչև հաջորդ փոփոխությունը։ Նախորդ օրերի բեռնագրերը չեն փոխվում։';
        markDriver(false);
        $('dpDriverDlg').showModal();
        $('dpDriverPick').focus();
    }
    // Выбор из списка (ответ владельца: «из ERP + добавить своих»): «— չկա —», экспедиторы ERP, свои, новый человек (поле
    // имени). Нынешний человек дня, которого в списке нет (давно не встречался), — среди своих.
    const DRIVER_NEW = '__new__';
    function fillDriverPick(role, current) {
        const c = CREW[role], sel = $(c.pick);
        sel.textContent = '';
        const list = Array.isArray(state.data.driver_list) ? state.data.driver_list.filter(x => isObj(x) && typeof x.name === 'string') : [];
        const own = list.filter(x => !x.erp).map(x => x.name);
        if (current && !list.some(x => x.name === current)) own.push(current);
        const add = (parent, value, text) => {
            const o = document.createElement('option');
            o.value = value;
            o.textContent = text;
            parent.appendChild(o);
        };
        add(sel, '', '— չկա —');
        [['ERP-ի առաքիչներ', list.filter(x => x.erp).map(x => x.name)], ['Ավելացված ծրագրում', own]].forEach(([label, names]) => {
            if (!names.length) return;
            const g = document.createElement('optgroup');
            g.label = label;
            names.forEach(n => add(g, n, n));
            sel.appendChild(g);
        });
        add(sel, DRIVER_NEW, c.newText);
        sel.value = current || '';
        $(c.box).hidden = true;
    }
    // role — чьё поле подсветить (null — ничьё)
    function markDriver(bad, text, role) {
        $('dpDriverErr').textContent = text || '';
        ROLES.forEach(r => [CREW[r].pick, CREW[r].input].forEach(id => $(id).removeAttribute('aria-invalid')));
        if (bad && role) $(fieldOf(role)).setAttribute('aria-invalid', 'true');
    }
    const fieldOf = (role) => ($(CREW[role].pick).value === DRIVER_NEW ? CREW[role].input : CREW[role].pick);
    const chosen = (role) => {
        const c = CREW[role], v = $(c.pick).value;
        return (v === DRIVER_NEW ? $(c.input).value : v).trim().replace(/\s+/g, ' ');
    };
    // Отправляются только изменённые роли, у каждой свой срок: другой человек — или тот же, но подмена становится
    // постоянной (отметку сняли). «— չկա —» — никого (в накладной строка водителя — вписать от руки). Ошибку исправляют,
    // выбрав верного человека в тот же день — запись дня заменяется
    async function saveDriver() {
        const code = state.driverCar;
        if (!code || state.busy) return;
        const past = !!state.data.is_past;
        const body = { date: state.day, car_code: code };
        for (const r of ROLES) {
            const c = CREW[r], name = chosen(r);
            if ($(c.pick).value === DRIVER_NEW && !name) {
                markDriver(true, c.needName, r);
                $(c.input).focus();
                return;
            }
            const oneDay = !past && $(c.dayBox).checked;
            if (name !== driverOf(code, r) || (isSub(code, r) && !past && !oneDay)) {
                body[c.field] = name;
                body[c.dayField] = oneDay;
            }
        }
        if (chosen('driver') && chosen('driver') === chosen('helper')) {
            markDriver(true, 'Վարորդն ու առաքիչը նույն մարդն են', 'helper');
            $(fieldOf('helper')).focus();
            return;
        }
        if (!ROLES.some(r => CREW[r].field in body)) { $('dpDriverDlg').close(); return; }
        const lock = ['dpDriverSave', 'dpDriverCancel', 'dpDriverPick', 'dpDriverName', 'dpHelperPick', 'dpHelperName', 'dpDriverOnlyDay', 'dpHelperOnlyDay'];
        state.busy = true;
        lock.forEach(id => { $(id).disabled = true; });
        markDriver(false);
        let r;
        try {
            // с таймаутом: пока идёт запрос, «Չեղարկել» и Esc не закрывают диалог
            r = await api('POST', '/api/routes/dispatch/driver', body, 30000);
        } catch (e) {
            state.busy = false;
            lock.forEach(id => { $(id).disabled = false; });
            const errs = e.data && isObj(e.data.errors) ? e.data.errors : {};
            const role = errs.helper ? 'helper' : errs.name ? 'driver' : null;
            markDriver(!!role, e.message, role);
            $(fieldOf(role || 'driver')).focus();
            return;
        }
        state.busy = false;
        lock.forEach(id => { $(id).disabled = false; });
        $('dpDriverDlg').close();
        if (state.data && state.data.day === r.day) {
            ['drivers', 'substitutes', 'helpers', 'helper_substitutes', 'driver_list', 'crew'].forEach(k => { state.data[k] = r[k]; });
            if (state.data.plan) renderTruckCards(state.data.plan);
            renderCrew();
            renderNoDriver();
            renderFresh();
        }
        const t = wbTruck(state.data, code) || truckBy(code);
        toast((t ? truckLabel(t) : code) + '՝ ' + (crewText(code) || 'վարորդ նշված չէ') + '։');
    }
    // ---------- Водители дня (ответ владельца №77) ----------
    // По умолчанию все вышли; снятая отметка — «не вышел» только этот день или до даты (диалог dpAbsentDlg), поставленная —
    // вышел. Сборка берёт столько машин, сколько вышло водителей (dispatch.build_crewed); изменили — пересобрать (crew.stale)
    function renderCrew() {
        const list = Array.isArray(crewOf().drivers) ? crewOf().drivers.filter(x => isObj(x) && typeof x.name === 'string') : [];
        const box = $('dpCrewList');
        box.textContent = '';
        $('dpCrew').hidden = !list.length;
        $('dpCrewCount').textContent = list.length ? 'եկել է՝ ' + list.filter(x => !x.absent).length + ' / ' + list.length : '';
        list.forEach(x => {
            const lab = document.createElement('label');
            lab.className = 'dp-truck dp-driver';
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = !x.absent;
            cb.disabled = !!state.data.is_past;
            cb.addEventListener('change', () => { if (cb.checked) savePresence(x.name, cb); else openAbsent(x, cb); });
            const txt = document.createElement('span');
            txt.className = 'dp-truck-t';
            const nm = document.createElement('b');
            nm.textContent = x.name;
            const sub = document.createElement('span');
            sub.className = 'dp-truck-sub';
            sub.textContent = (Array.isArray(x.trucks) ? x.trucks : []).map(c => truckLabel(truckBy(c))).join(', ');
            txt.append(nm, sub);
            if (x.absent) {
                const off = document.createElement('span');
                off.className = 'dp-truck-sub is-warn';
                off.textContent = x.until && x.until !== state.data.day ? 'չի աշխատում մինչև ' + dateRu(x.until) + ' ներառյալ' : 'չի եկել';
                txt.appendChild(off);
            }
            lab.append(cb, txt);
            box.appendChild(lab);
        });
    }
    function openAbsent(x, cb) {
        if (state.busy) { cb.checked = true; return; }
        state.absent = { name: x.name, cb, saved: false };
        $('dpAbsentLead').textContent = x.name + (Array.isArray(x.trucks) && x.trucks.length ? ' · ' + x.trucks.map(c => truckLabel(truckBy(c))).join(', ') : '');
        $('dpAbsentOneT').textContent = 'Միայն ' + dayHuman(state.day) + 'ն';
        $('dpAbsentOne').checked = true;
        $('dpAbsentUntil').min = state.day;
        $('dpAbsentUntil').max = shiftDay(state.day, 366);      // как сервер: не дальше года (DRIVER_ABSENCE_MAX_DAYS)
        $('dpAbsentUntil').value = shiftDay(state.day, 1);
        $('dpAbsentErr').textContent = '';
        $('dpAbsentUntil').removeAttribute('aria-invalid');
        $('dpAbsentDlg').showModal();
        $('dpAbsentOne').focus();
    }
    async function saveCrew(body) {
        state.busy = true;
        try {
            return await api('POST', '/api/routes/dispatch/absence', Object.assign({ date: state.day }, body), 30000);
        } finally {
            state.busy = false;
        }
    }
    function crewSaved(r) {
        if (state.data && state.data.day === r.day) {
            state.data.crew = r.crew;
            renderCrew();
            if (state.data.plan) renderTruckCards(state.data.plan);
            renderFresh();
        }
    }
    async function saveAbsent() {
        const a = state.absent;
        if (!a || state.busy) return;
        const long = $('dpAbsentLong').checked, until = $('dpAbsentUntil').value;
        if (long && !(/^\d{4}-\d{2}-\d{2}$/.test(until) && until >= state.day && until <= $('dpAbsentUntil').max)) {
            $('dpAbsentErr').textContent = 'Նշեք օրը, մինչև որը վարորդը չի աշխատի (ոչ շուտ, քան ' + dateRu(state.day) + ')';
            $('dpAbsentUntil').setAttribute('aria-invalid', 'true');
            $('dpAbsentUntil').focus();
            return;
        }
        const lock = ['dpAbsentSave', 'dpAbsentCancel', 'dpAbsentOne', 'dpAbsentLong', 'dpAbsentUntil'];
        lock.forEach(id => { $(id).disabled = true; });
        let r;
        try {
            r = await saveCrew(Object.assign({ name: a.name, absent: true }, long ? { until } : {}));
        } catch (e) {
            $('dpAbsentErr').textContent = e.message;
            return;
        } finally {
            lock.forEach(id => { $(id).disabled = false; });
        }
        a.saved = true;
        $('dpAbsentDlg').close();
        crewSaved(r);
        toast(a.name + '՝ ' + (long ? 'չի աշխատում մինչև ' + dateRu(until) + ' ներառյալ' : dayHuman(state.day) + 'ն չի եկել') + '։' + (state.data.plan ? ' Վերակազմեք երթերը։' : ''));
    }
    async function savePresence(name, cb) {
        if (state.busy) { cb.checked = false; return; }
        let r;
        try {
            r = await saveCrew({ name, absent: false });
        } catch (e) {
            cb.checked = false;
            showActionError(e);
            return;
        }
        crewSaved(r);
        toast(name + '՝ եկել է։' + (state.data.plan ? ' Վերակազմեք երթերը։' : ''));
    }
    const wbBasis = (tr) => tr.stops.map(s => [s.customer_id, s.share, (s.orders || []).map(o => o.isn).sort()]);
    const wbStale = () => Object.assign(new Error(WB_STALE_HY), { status: 409, data: null });
    const wbTruck = (d, code) => (d && d.plan ? d.plan.trucks.find(x => x.car_code === code) : null);
    // Занята ли кнопка: не disabled (фокус клавиатуры остаётся на ней, setBusy страницы её состояние не путает)
    const wbBusy = (btn) => btn.getAttribute('aria-busy') === 'true';
    async function fetchWaybill(code, btn) {
        const d = state.data;
        if (!wbTruck(d, code)) throw wbStale();
        btn.setAttribute('aria-busy', 'true');
        let wb;
        try {
            wb = await api('GET', '/api/routes/dispatch/waybill?' + new URLSearchParams({ date: d.day, truck: code, rev: String(d.rev) }));
        } finally {
            btn.removeAttribute('aria-busy');
        }
        // сверка — с планом на экране ПОСЛЕ ответа: пока шёл запрос, логист мог перенести магазин или день перечитался
        const cur = state.data, t = wbTruck(cur, code);
        const same = !!t && cur.day === d.day && cur.rev === wb.rev && Array.isArray(wb.trips) && wb.trips.length === t.trips.length
            && wb.trips.every((x, i) => x.id === t.trips[i].id && JSON.stringify(x.basis) === JSON.stringify(wbBasis(t.trips[i])));
        if (!same) throw wbStale();
        return { t, d: cur, wb };
    }
    // лист печати, имя товара и примечания — общий рендер base.js (тот же документ у склада «Պահեստ»); берётся при вызове:
    // base.js не загрузился — ломаются только Բեռնագիր и Excel, а не вся страница
    const wbName = (r) => window.RtWaybill.name(r);
    const wbNotes = (tr) => window.RtWaybill.notes(tr);
    async function printWaybill(code, btn, send) {
        if (wbBusy(btn) || state.busy) return;
        hideActionError();
        // окно — сразу по нажатию: открытое после ответа сервера браузер счёл бы всплывающим и заблокировал
        const w = window.open('', '_blank');
        if (!w) { showActionError(new Error('Զննարկիչը թույլ չտվեց բացել տպման պատուհանը — թույլատրեք թռուցիկ պատուհանները այս կայքի համար։')); return; }
        w.document.write('<!doctype html><html lang="hy"><head><meta charset="utf-8"><title>Բեռնագիր</title></head>'
            + '<body style="font-family:Segoe UI,Sylfaen,Arial,sans-serif;padding:24px">Բեռնագիրը պատրաստվում է…</body></html>');
        w.document.close();
        // №81: сначала отправить правки водителям — накладная по плану, который у водителя
        if (send && !(await sendForPaper())) { try { w.close(); } catch (e) { /* уже закрыто */ } return; }
        let res;
        try { res = await fetchWaybill(code, btn); } catch (e) {
            try { w.close(); } catch (x) { /* уже закрыто */ }
            showActionError(e);
            return;
        }
        if (w.closed) return;
        w.document.open();
        w.document.write(window.RtWaybill.html(res.t, res.d, res.wb));
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* окно закрыли раньше */ } }, 300);
    }
    async function excelWaybill(code, btn, send) {
        if (wbBusy(btn) || state.busy) return;
        if (send && !(await sendForPaper())) return;
        hideActionError();
        if (typeof window.XLSX === 'undefined') { showActionError(new Error('Excel-ի գրադարանը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։')); return; }
        let res;
        try { res = await fetchWaybill(code, btn); } catch (e) { showActionError(e); return; }
        const { t, d, wb } = res;
        const book = XLSX.utils.book_new();
        wb.trips.forEach(tr => {
            const rows = [['Բեռնագիր'], ['Մեքենա', truckLabel(t)], ['Վարորդ', (wb.driver || '') + (wb.driver && wb.driver_seat ? ' (փոխարինում)' : '')], ['Առաքիչ', wb.helper || ''], ['Օր', (WD_NAME[d.weekday] || '') + ', ' + dateRu(d.day)],
                ['Երթ', tr.no + (wb.trips.length > 1 ? ' / ' + wb.trips.length : '')], ['Բեռնում', tr.loading_start], ['Մեկնում', tr.depart],
                ['Խանութներ', tr.stops], [],
                ['№', 'Կոդ', 'Ապրանք', 'Միավոր', 'Քանակ', 'Փաթեթ', 'Առանձին', 'Փաթեթում', 'Քաշ, կգ']];
            tr.rows.forEach((r, i) => rows.push([i + 1, r.code, wbName(r), r.unit, r.qty,
                r.pack && r.packs !== null ? r.packs : '', r.pack && r.packs !== null ? r.loose : '', r.pack || '', r.kg]));
            rows.push(['', '', 'Ընդամենը', '', '', '', '', '', tr.kg], []);
            wbNotes(tr).forEach(x => rows.push([x]));
            const ws = XLSX.utils.aoa_to_sheet(rows);
            ws['!cols'] = [{ wch: 5 }, { wch: 8 }, { wch: 42 }, { wch: 8 }, { wch: 9 }, { wch: 8 }, { wch: 9 }, { wch: 10 }, { wch: 9 }];
            XLSX.utils.book_append_sheet(book, ws, 'Երթ ' + tr.no);
        });
        XLSX.writeFile(book, 'bernagir_' + (t.car_code.replace(/[^0-9A-Za-z]+/g, '') || 'mekena') + '_' + d.day + '.xlsx');
        announce('Բեռնագիրը ներբեռնված է՝ ' + truckLabel(t));
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

    let aiPanel = null;                     // панель «Հարցրու AI-ին» — routes_dispatch_ai.js

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
        $('dpAgentsAll').addEventListener('click', () => pickAllAgents(true));
        $('dpAgentsNone').addEventListener('click', () => pickAllAgents(false));
        $('dpAgentsApply').addEventListener('click', applyAgents);
        $('dpRuleApply').addEventListener('click', () => edit({ action: 'apply_settings' }, 'Օրվա ընտրությունը համապատասխանեցվեց կարգավորումներին'));
        $('dpAgentsUndo').addEventListener('click', () => { state.agentsPick = null; renderAgents(); renderOrders(); });
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
        $('dpDriverSave').addEventListener('click', () => saveDriver());
        $('dpAbsentSave').addEventListener('click', () => saveAbsent());
        $('dpNoDriverBtn').addEventListener('click', () => { const t = noDriver()[0]; if (t) openDriver(t.car_code); });
        $('dpAbsentCancel').addEventListener('click', () => $('dpAbsentDlg').close());
        $('dpAbsentDlg').addEventListener('cancel', (e) => { if (state.busy) e.preventDefault(); });
        $('dpAbsentDlg').addEventListener('close', () => {
            const a = state.absent;
            state.absent = null;
            if (a && !a.saved) a.cb.checked = true;       // не сохранили — водитель по-прежнему вышел
            if (a && document.contains(a.cb)) a.cb.focus();
        });
        $('dpAbsentUntil').addEventListener('input', () => { $('dpAbsentLong').checked = true; $('dpAbsentErr').textContent = ''; });
        $('dpDriverCancel').addEventListener('click', () => $('dpDriverDlg').close());
        $('dpSdClose').addEventListener('click', () => $('dpSameDayDlg').close());
        $('dpSameDayDlg').addEventListener('close', () => { state.sd = null; });
        $('dpDriverDlg').addEventListener('close', () => {
            const card = [...$('dpTruckCards').querySelectorAll('.dp-tcard')].find(c => c.dataset.truck === state.driverCar);
            const btn = card && card.querySelector('.dp-drvbtn');
            state.driverCar = null;
            // фокус — обратно на кнопку «Վարորդ» этой машины или на «Նշել» строки «վարորդ նշված չէ» (№77)
            if (btn) btn.focus(); else if (!$('dpNoDriver').hidden) $('dpNoDriverBtn').focus();
        });
        $('dpDriverDlg').addEventListener('cancel', (e) => { if (state.busy) e.preventDefault(); });
        // поле нового человека — без перевода фокуса: стрелки по закрытому списку тоже дают change (WCAG 3.2.2)
        ROLES.forEach(r => {
            const c = CREW[r];
            $(c.input).addEventListener('input', () => markDriver(false));
            $(c.input).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveDriver(); } });
            $(c.pick).addEventListener('change', () => {
                $(c.box).hidden = $(c.pick).value !== DRIVER_NEW;
                markDriver(false);
            });
        });
        $('dpUnloadSave').addEventListener('click', () => saveUnload(false));
        $('dpUnloadClear').addEventListener('click', () => saveUnload(true));
        $('dpUnloadCancel').addEventListener('click', () => $('dpUnloadDlg').close());
        $('dpUnloadDlg').addEventListener('close', () => { state.unloadStop = null; state.unloadInfo = null; });
        $('dpUnloadDlg').addEventListener('cancel', (e) => { if (state.busy) e.preventDefault(); });
        $('dpUnloadMin').addEventListener('input', () => { $('dpUnloadErr').textContent = ''; markUnload(false); });
        $('dpUnloadMin').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveUnload(false); } });
        $('dpMapBox').addEventListener('toggle', () => { if ($('dpMapBox').open && state.data && state.data.plan) drawMap(); });
        initTabs();
        initWs();
        dropStops();
        initStopHover();
        $('dpSfSend').addEventListener('click', () => {
            const go = paperNext;
            paperNext = null;
            $('dpSendFirstDlg').close();
            if (go) go(true);
        });
        $('dpSfCancel').addEventListener('click', () => $('dpSendFirstDlg').close());
        $('dpSendFirstDlg').addEventListener('close', () => { paperNext = null; });
        try { state.boardFolded = window.localStorage.getItem('dpBoardFolded') === '1'; } catch (e) { /* хранилище недоступно */ }
        $('dpStep1Tog').addEventListener('click', () => toggleStep('dpStep1'));
        $('dpStep2Tog').addEventListener('click', () => toggleStep('dpStep2'));
        // высота закреплённой шапки дашборда — карта закрепляется под ней (меню на узком экране раскрывается — пересчитать)
        const nav = document.querySelector('.navbar');
        const navH = () => $('rtDispatch').style.setProperty('--rt-nav-h',
            (nav && getComputedStyle(nav).position === 'sticky' ? Math.ceil(nav.getBoundingClientRect().height) : 0) + 'px');
        navH();
        if (nav && typeof window.ResizeObserver !== 'undefined') new ResizeObserver(navH).observe(nav); else window.addEventListener('resize', navH);
        aiPanel = window.RoutesDispatchAI ? window.RoutesDispatchAI.attach({ $, state, api, announce, dayHuman, truckLabel, load }) : null;
        setInterval(poll, 30 * 1000);       // poll() сам проверяет, прошло ли 5 минут (и после сна компьютера тоже)
        setInterval(loadProgress, PROGRESS_MS);   // №82: ход дня на шкале — сегодня
        document.addEventListener('visibilitychange', () => { if (!document.hidden) loadProgress(); });
        document.addEventListener('visibilitychange', poll);
        load(day);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

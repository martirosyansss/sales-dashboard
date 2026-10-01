/* «Развоз» /routes/dispatch — план развоза на завтра (docs/plans/dispatch-plan.md).
   Данные: GET /api/routes/dispatch?date=…; «Собрать рейсы» — POST /api/routes/dispatch/build;
   правки логиста — POST /api/routes/dispatch/edit (move | pin | unpin | exclude | include, с номером
   черновика rev); «Начать заново» — POST /api/routes/dispatch/reset; ручная точка магазина —
   POST /api/routes/geo-override; «План и факт» — GET /api/routes/dispatch/fact?date=….
   Раз в 5 минут, пока страница открыта, — GET /api/routes/dispatch/status?date=…: «заказы ещё поступают»
   и сколько заказов пришло или ушло с последней сборки. Рейсы сами не пересобираются — только по кнопке.
   Безопасность: всё, что пришло из ERP (магазины, адреса, менеджеры, машины), выводится только через
   esc() или textContent — в том числе в попапах карты, листах для водителей и Excel. POST — только JSON.
   Язык страницы — армянский (ответ владельца №31). Ошибки сервера приходят по-русски — переводятся
   словарём SERVER_HY; незнакомый текст показывается как есть.
   Интерфейс «для чайников»: вверху «Ի՞նչ անել հիմա» — одна подсказка и главная кнопка по состоянию дня;
   рейсы — простой список, как лист водителя; правки — по кнопке «Փոփոխել» у рейса. */
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
    const dateRu = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    const wdOf = (s) => { const d = new Date(s + 'T12:00:00'); return Number.isNaN(d.getTime()) ? null : (d.getDay() || 7); };
    // Цвета машин: тот же ряд, что у менеджеров в «Обзоре» (различимы и при дальтонизме)
    const COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3', '#0d9298', '#b8b90c', '#ae55c1', '#37981b'];
    const YEREVAN = [40.1792, 44.4991];
    const POLL_MS = 5 * 60 * 1000;          // автообновление заказов дня
    const MANAGERS_DONE = '16:40';          // владелец: менеджеры заканчивают день ≈ 16:40

    const state = {
        day: null, data: null, busy: false,
        map: null, layers: null, mapFailed: false,
        roadCache: new Map(), roadGen: 0,   // линии рейсов по дорогам: ключ — точки линии; номер отрисовки
        pickMap: null, pickMarker: null, pickCid: null,
        loadSeq: 0,                         // номер последнего запроса дня: ответы на прежние запросы не применяются
        editing: new Set(),                 // рейсы, открытые кнопкой «Փոփոխել»
        fetchedAt: 0,                       // когда последний раз спрашивали сервер о заказах дня
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
    };
    const SERVER_HY_PREFIX = [['машина не готова к расчёту: ', 'Մեքենան պատրաստ չէ հաշվարկի համար՝ ']];
    function serverText(s) {
        const t = String(s).trim();
        if (Object.prototype.hasOwnProperty.call(SERVER_HY, t)) return SERVER_HY[t];
        const p = SERVER_HY_PREFIX.find(([ru]) => t.startsWith(ru));
        return p ? p[1] + t.slice(p[0].length) : t;
    }
    async function api(method, url, body) {
        const opts = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let resp;
        try { resp = await fetch(url, opts); } catch (e) {
            throw Object.assign(new Error('Սերվերի հետ կապ չկա։'), { status: 0, data: null });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
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
            setData(data);
            $('dpBody').hidden = false;
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
        if (data.day !== state.day || !data.plan) state.editing.clear();
        else [...state.editing].forEach(id => { if (!trips.has(id)) state.editing.delete(id); });
        state.data = data;
        state.day = data.day;
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
    function render() {
        const d = state.data;
        const rel = relDay(d.day);
        $('dpTitle').textContent = (rel ? rel + '՝ ' : '') + dayHuman(d.day, true);
        document.title = 'Առաքում ' + dateRu(d.day) + ' — Sales Dashboard';
        $('dpDate').value = d.day;
        $('dpTomorrow').hidden = d.day === d.default_day;
        renderDateNote();
        renderTrucks();
        renderOrders();
        renderNoCoords();
        renderOrderLists();
        const plan = d.plan;
        $('dpBuildText').textContent = plan ? 'Վերակազմել երթերը' : 'Կազմել երթերը';
        $('dpReset').hidden = !plan;
        $('dpBuild').disabled = !d.trucks.some(t => t.ready) || !d.depot;
        $('dpBuildNote').textContent = plan ? 'Ամրացված երթերը կմնան ինչպես կան, մնացածը ծրագիրը կբաշխի նորից։' : 'Մոտ 5 վայրկյան։';
        $('dpStep1').classList.toggle('is-done', !!plan);
        $('dpStep2').classList.toggle('is-done', !!plan);
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
        plan.trucks.forEach(t => t.trips.forEach(tr => { if (tr.over_time || tr.over_capacity || tr.no_truck) n++; }));
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
        return state.pickCid !== null || (!!a && $('dpBody').contains(a) && /^(SELECT|INPUT|TEXTAREA)$/.test(a.tagName));
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
            plate.className = 'dp-truck-sub';
            plate.textContent = t.name ? t.car_code : '';
            const sub = document.createElement('span');
            sub.className = 'dp-truck-sub';
            sub.textContent = t.ready ? 'տանում է մինչև ' + kgText(t.capacity_kg) : 'լրացրեք կարգավորումներում';
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
        $('dpTrucksCount').textContent = ready ? 'Նշված է՝ ' + selectedTrucks().length + ' / ' + ready : '';
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
        statTiles($('dpOrderStats'), [[fmt(o.count), 'պատվեր'], [fmt(o.customers), 'խանութ'], [kgText(o.kg), 'քաշը'], [money(o.revenue), 'գումարը']]);
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
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
        map.setView(state.data.depot ? [state.data.depot.lat, state.data.depot.lon] : YEREVAN, 11);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        map.on('click', (e) => {
            if (state.pickCid === null) return;
            setPick(e.latlng.lat, e.latlng.lng);
        });
        state.pickMap = map;
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
            ['≈ ' + fmt(sm.km) + NB + 'կմ', 'ճանապարհ'], ['≈ ' + fmt(sm.liters) + NB + 'լ', 'դիզել']]);
        const lead = $('dpMainLead');
        lead.className = 'dp-summary-lead';
        lead.textContent = '';
        if (base && num(base.km) !== null) {
            const diff = Math.round(base.km - sm.km);
            const dl = base.liters !== null ? Math.round(base.liters - sm.liters) : 0;
            if (diff > 0) lead.textContent = 'Սա ' + fmt(diff) + NB + 'կմ-ով կարճ է, քան եթե յուրաքանչյուր մենեջերի պատվերները տաներ իր սովորական մեքենան'
                + (dl > 0 ? ' (≈ ' + fmt(dl) + NB + 'լ դիզելի խնայողություն)' : '') + '։';
            else {
                lead.classList.add('is-neutral');
                lead.textContent = diff < 0
                    ? 'Սա ' + fmt(-diff) + NB + 'կմ-ով երկար է, քան սովորական բաշխումը ըստ մենեջերների — ստուգեք ամրացված երթերը։'
                    : 'Ճանապարհը նույնն է, ինչ սովորական բաշխումը ըստ մենեջերների։';
            }
        }
        renderOverflow(plan);
        renderUnassigned(plan);
        renderTruckCards(plan);
        renderBaseline(plan);
        if (!$('dpMapBox').open) return;
        drawMap();
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
        }));
        if (!bad.length) return;
        const div = document.createElement('div');
        div.className = 'rt-alert is-warn dp-problem';
        div.innerHTML = '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i><div class="rt-alert-text"><b>Չի տեղավորվել</b><ul></ul>'
            + '<p class="dp-problem-do">Ինչ անել՝ 1-ին քայլում նշեք ևս մեկ մեքենա և սեղմեք «Վերակազմել երթերը», կամ սեղմեք «Փոփոխել» երթի մոտ և տեղափոխեք խանութները այլ երթ։</p></div>';
        const ul = div.querySelector('ul');
        bad.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
        box.appendChild(div);
    }

    // Все рейсы плана — для списка «Перенести в…»
    function tripOptions(currentTrip, placeholder) {
        const opts = [['', placeholder]];
        state.data.plan.trucks.forEach(t => t.trips.forEach((tr, i) => {
            if (tr.id !== currentTrip) opts.push(['t:' + tr.id, truckLabel(t) + ' · երթ ' + (i + 1)]);
        }));
        state.data.trucks.filter(t => t.selected).forEach(t => opts.push(['n:' + t.car_code, 'Նոր երթ · ' + truckLabel(t)]));
        if (currentTrip !== null) opts.push(['u:', 'Հանել երթից (կմնա «դեռ երթում չէ»)']);
        return opts;
    }
    function moveSelect(stop, tripId) {
        const sel = document.createElement('select');
        sel.className = 'rt-select dp-move';
        const placeholder = tripId === null ? 'Ավելացնել երթին…' : 'Տեղափոխել այլ երթ…';
        sel.setAttribute('aria-label', placeholder + ' «' + (stop.name || stop.code) + '»');
        tripOptions(tripId, placeholder).forEach(([v, t]) => sel.add(new Option(t, v)));
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

    // Одна точка — как строка листа водителя: №, магазин, адрес, кг; в режиме правки — действия
    function stopItem(stop, idx, tripId, editing) {
        const li = document.createElement('li');
        li.className = 'dp-stop';
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
        if (stop.coord_source === 'manual') {
            const bd = document.createElement('span');
            bd.className = 'rt-badge b-manual';
            bd.textContent = 'տեղը նշված է ձեռքով';
            main.appendChild(bd);
        }
        const kg = document.createElement('span');
        kg.className = 'dp-stop-kg';
        kg.textContent = kgText(stop.kg) + (stop.share > 1 ? ' (1/' + stop.share + ')' : '');
        li.append(no, main, kg);
        if (editing) {
            const acts = document.createElement('div');
            acts.className = 'dp-stop-acts';
            const ex = document.createElement('button');
            ex.type = 'button';
            ex.className = 'rt-btn rt-btn-ghost rt-btn-sm';
            ex.innerHTML = '<i class="fas fa-ban" aria-hidden="true"></i><span>Այսօր չենք տանում</span>';
            ex.setAttribute('aria-label', 'Այսօր չենք տանում՝ ' + (stop.name || stop.code));
            ex.addEventListener('click', () => excludeStop(stop));
            acts.append(moveSelect(stop, tripId), ex);
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
        // Не поместились до конца рабочего дня выбранных машин (сборка за конец дня не планирует) — отдельно
        const noRoom = plan.unassigned.filter(s => s.no_room), other = plan.unassigned.filter(s => !s.no_room);
        const kgOf = (list) => list.reduce((a, s) => a + (s.kg || 0), 0);
        if (noRoom.length) {
            const card = document.createElement('section');
            card.className = 'rt-card dp-unassigned';
            card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i>Չտեղավորվեցին մինչև '
                + esc(state.data.work_end) + '-ը</h3><span class="rt-card-state is-bad"></span></div>';
            card.querySelector('.rt-card-state').textContent = pl(noRoom.length, 'խանութ') + ', ' + kgText(kgOf(noRoom));
            // Сначала — ещё машина; форс-мажор (ответ владельца №32) — везти после конца дня, до предела
            const unpicked = state.data.trucks.filter(t => t.ready && !t.selected).length;
            const changed = trucksChanged();
            const month = state.data.overtime_days_month || 0;
            const monthText = month ? ' Այս ամիս արտաժամյա՝ ' + pl(month, 'օր') + '։' : '';
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
            card.append(lead);
            if (!state.data.overtime_ok && !changed) {
                const p = document.createElement('p');
                p.className = 'rt-card-lead';
                p.textContent = 'Եթե այս պատվերները պետք է տանել այսօր, մեքենաները կաշխատեն ' + state.data.work_end + '-ից հետո՝ մինչև '
                    + state.data.overtime_end + '-ը։ Սա բացառություն է, ոչ թե ամենօրյա կարգ։' + monthText;
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'rt-btn rt-btn-ghost dp-overtime-btn';
                btn.innerHTML = '<i class="fas fa-moon" aria-hidden="true"></i> Տանել ' + esc(state.data.work_end) + '-ից հետո';
                btn.addEventListener('click', overtime);
                card.append(p, btn);
            }
            card.appendChild(stopList(noRoom, null, true));
            box.appendChild(card);
        }
        if (!other.length) return;
        const card = document.createElement('section');
        card.className = 'rt-card dp-unassigned';
        card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas fa-inbox" aria-hidden="true"></i>Դեռ ոչ մի երթում չեն</h3><span class="rt-card-state is-todo"></span></div>'
            + '<p class="rt-card-lead">Նոր կամ վերադարձված պատվերներ, կամ խանութներ, որոնց տեղը նոր եք նշել։ Յուրաքանչյուրի համար ընտրեք՝ որ երթին ավելացնել, '
            + 'կամ պարզապես սեղմեք «Վերակազմել երթերը» 2-րդ քայլում։</p>';
        card.querySelector('.rt-card-state').textContent = pl(other.length, 'խանութ') + ', ' + kgText(kgOf(other));
        card.appendChild(stopList(other, null, true));
        box.appendChild(card);
    }

    function renderTruckCards(plan) {
        const box = $('dpTruckCards');
        box.textContent = '';
        plan.trucks.forEach(t => {
            const card = document.createElement('section');
            card.className = 'rt-card dp-tcard';
            card.style.setProperty('--dp-c', truckColor(t.car_code));
            const head = document.createElement('div');
            head.className = 'dp-thead';
            const h = document.createElement('h3');
            h.className = 'rt-card-title';
            h.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span></span>';
            h.querySelector('.rt-dot').style.background = truckColor(t.car_code);
            h.lastChild.textContent = truckLabel(t);
            const st = document.createElement('p');
            st.className = 'dp-tstats';
            st.textContent = pl(t.trips.length, 'երթ') + ' · ' + pl(t.stops, 'խանութ') + ' · ' + kgText(t.kg)
                + ' · ≈ ' + fmt(t.km) + NB + 'կմ · վերադարձ՝ ' + t.return;
            if (t.over_time) st.classList.add('is-bad');
            head.append(h, st);
            card.appendChild(head);
            t.trips.forEach((tr, i) => card.appendChild(tripBlock(t, tr, i)));
            box.appendChild(card);
        });
    }

    function tripBlock(t, tr, i) {
        const editing = state.editing.has(tr.id);
        const div = document.createElement('div');
        div.className = 'dp-trip' + (tr.over_time || tr.over_capacity ? ' is-bad' : '') + (editing ? ' is-editing' : '');
        const head = document.createElement('div');
        head.className = 'dp-trip-head';
        const title = document.createElement('h4');
        title.className = 'dp-trip-t';
        title.textContent = 'Երթ ' + (i + 1);
        const time = document.createElement('span');
        time.className = 'dp-trip-time';
        time.textContent = 'մեկնում ' + tr.depart + ' → վերադարձ ' + tr.return;
        const meta = document.createElement('span');
        meta.className = 'dp-trip-meta';
        meta.textContent = kgText(tr.kg) + (tr.load_pct !== null ? ' (մեքենան լցված է ' + tr.load_pct + '%-ով)' : '') + ' · ≈ ' + fmt(tr.km) + NB + 'կմ';
        const flags = document.createElement('span');
        flags.className = 'dp-trip-flags';
        if (tr.over_time) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">ուշանում է</span>');
        else if (tr.late) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn"><i class="fas fa-moon" aria-hidden="true"></i>արտաժամյա</span>');
        if (tr.over_capacity) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">գերբեռնված</span>');
        if (tr.poor) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-warn">' + esc(fmt(state.data.min_trip_revenue)) + NB + 'դրամից պակաս</span>');
        if (tr.pinned) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-ok"><i class="fas fa-lock" aria-hidden="true"></i>ամրացված</span>');
        const tog = document.createElement('button');
        tog.type = 'button';
        tog.className = 'rt-btn rt-btn-ghost rt-btn-sm dp-editbtn';
        tog.dataset.trip = String(tr.id);
        tog.setAttribute('aria-expanded', String(editing));
        tog.innerHTML = '<i class="fas ' + (editing ? 'fa-check' : 'fa-pen') + '" aria-hidden="true"></i><span></span>';
        tog.lastChild.textContent = editing ? 'Պատրաստ է' : 'Փոփոխել';
        tog.setAttribute('aria-label', (editing ? 'Ավարտել փոփոխությունը՝ ' : 'Փոփոխել՝ ') + truckLabel(t) + ', երթ ' + (i + 1));
        tog.addEventListener('click', () => toggleEdit(tr.id));
        head.append(title, time, meta, flags, tog);
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
        const working = state.data.trucks.filter(x => x.selected);
        if (!working.some(x => x.car_code === t.car_code)) sel.add(new Option('Ընտրեք մեքենա…', '', true, true));
        working.forEach(x => sel.add(new Option(truckLabel(x), x.car_code, false, x.car_code === t.car_code)));
        sel.addEventListener('change', () => sel.value && edit({ action: 'pin', trip: tr.id, truck: sel.value }, 'Երթը տրվեց մեքենային՝ ' + truckLabel(truckBy(sel.value))));
        lab.appendChild(sel);
        const pin = document.createElement('button');
        pin.type = 'button';
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
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
        map.setView(YEREVAN, 10);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.layers = L.layerGroup().addTo(map);
    }
    function drawMap() {
        ensureMap();
        if (!state.map) return;
        const d = state.data, plan = d.plan;
        state.layers.clearLayers();
        state.map.invalidateSize();
        const bounds = [];
        const depot = d.depot ? [d.depot.lat, d.depot.lon] : null;
        const legend = $('dpLegend');
        legend.textContent = '';
        const routes = [];   // [линия на карте, точки рейса по порядку]
        plan.trucks.forEach(t => {
            const color = truckColor(t.car_code);
            const lg = document.createElement('span');
            lg.className = 'dp-lg';
            lg.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span></span>';
            lg.firstChild.style.background = color;
            lg.lastChild.textContent = truckLabel(t) + ' — ' + pl(t.trips.length, 'երթ');
            legend.appendChild(lg);
            t.trips.forEach((tr, ti) => {
                const pts = tr.stops.filter(s => s.lat !== null).map(s => [s.lat, s.lon]);
                const line = depot ? [depot, ...pts, depot] : pts;
                if (line.length > 1) routes.push([L.polyline(line, { color, weight: 3, opacity: .85, dashArray: ti % 2 ? '6 6' : null }).addTo(state.layers), line]);
                tr.stops.forEach((s, si) => {
                    if (s.lat === null) return;
                    bounds.push([s.lat, s.lon]);
                    const m = L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: color, fillOpacity: 1 });
                    m.bindTooltip(esc(truckLabel(t)) + ' · երթ ' + (ti + 1) + ' · №' + (si + 1) + '<br>' + esc(s.name || s.code));
                    m.bindPopup('<div class="rt-pop"><b>' + esc(s.name || s.code) + '</b><br>' + esc(s.address || '') + '<br>'
                        + esc(kgText(s.kg)) + ' · ' + esc(money(s.revenue)) + '<br>' + esc(truckLabel(t)) + ', երթ ' + (ti + 1) + ', կետ ' + (si + 1) + '</div>');
                    m.addTo(state.layers);
                });
            });
        });
        plan.unassigned.forEach(s => {
            if (s.lat === null) return;
            bounds.push([s.lat, s.lon]);
            L.circleMarker([s.lat, s.lon], { radius: 7, color: s.no_room ? '#ff6b79' : '#ffb547', weight: 3, fillColor: '#0c0f14', fillOpacity: 1 })
                .bindTooltip((s.no_room ? 'Չտեղավորվեց մինչև ' + esc(state.data.work_end) + '-ը՝ ' : 'Դեռ երթում չէ՝ ') + esc(s.name || s.code)).addTo(state.layers);
        });
        if (depot) {
            bounds.push(depot);
            L.marker(depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<i class="fas fa-warehouse"></i>', iconSize: [28, 28] }), keyboard: false })
                .bindTooltip('Պահեստ').addTo(state.layers);
        }
        if (plan.unassigned.length) {
            const lg = document.createElement('span');
            lg.className = 'dp-lg';
            lg.innerHTML = '<span class="rt-dot dp-dot-open" aria-hidden="true"></span><span>դեռ երթում չէ</span>';
            legend.appendChild(lg);
        }
        if (bounds.length) state.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 });
        roadLines(routes);
    }

    // Рейсы сначала рисуются по прямой, затем линии заменяются ответом сервера — по дорогам. Нет связи
    // или карты дорог на сервере — остаются прямые. Ответ устарел (карту перерисовали) — не применяется.
    async function roadLines(routes) {
        const gen = ++state.roadGen;
        const key = line => line.map(p => p[0].toFixed(5) + ',' + p[1].toFixed(5)).join(';');
        const missing = routes.filter(([, line]) => !state.roadCache.has(key(line)));
        if (missing.length) {
            let data;
            try { data = await api('POST', '/api/routes/road-lines', { lines: missing.map(([, line]) => line) }); } catch (e) { return; }
            if (!Array.isArray(data.lines)) return;
            missing.forEach(([, line], i) => state.roadCache.set(key(line), data.lines[i]));
        }
        if (gen !== state.roadGen) return;
        routes.forEach(([pl, line]) => {
            const road = state.roadCache.get(key(line));
            if (Array.isArray(road) && road.length > 1) pl.setLatLngs(road);
        });
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
            const late = data.plan.unassigned.filter(s => s.no_room).length, rest = data.plan.unassigned.length - late;
            toast(late ? pl(late, 'խանութ') + ' չեն հասցնում նույնիսկ մինչև ' + data.overtime_end + '-ը։'
                : rest ? pl(rest, 'խանութ') + ' դեռ ոչ մի երթում չեն։'
                : 'Բոլոր խանութները երթերում են' + (data.overtime ? '՝ արտաժամյա' : '') + '։');
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
            + 'td.ok{width:60px}.addr{font-size:17px}.nm{font-weight:700}@media screen{body{background:#fff}}'
            + '</style></head><body>';
        plan.trucks.forEach(t => {
            html += '<section class="sheet"><h1>' + esc(truckLabel(t)) + '</h1><p class="sub">Առաքում՝ ' + esc(dayText) + ' · '
                + esc(pl(t.trips.length, 'երթ')) + ' · ' + esc(pl(t.stops, 'կետ')) + ' · ' + esc(kgText(t.kg))
                + ' · ≈ ' + esc(fmt(t.km)) + ' կմ</p>';
            t.trips.forEach((tr, i) => {
                html += '<h2>Երթ ' + (i + 1) + '՝ մեկնում ' + esc(tr.depart) + ', ' + esc(kgText(tr.kg)) + ', ≈ ' + esc(fmt(tr.km)) + ' կմ</h2>'
                    + '<table><thead><tr><th>№</th><th>Խանութ և հասցե</th><th>Բեռ</th><th>Նշում</th></tr></thead><tbody>';
                tr.stops.forEach((s, si) => {
                    html += '<tr><td class="n">' + (si + 1) + '</td><td><div class="nm">' + esc(s.name || s.code) + ' <small>(' + esc(s.code) + ')</small></div>'
                        + '<div class="addr">' + esc(s.address || 'ERP-ում հասցե չկա') + '</div></td><td class="kg">' + esc(kgText(s.kg)) + '</td><td class="ok"></td></tr>';
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
        const rows = [['Մեքենա', 'Երթ', 'Մեկնում', 'Վերադարձ', '№', 'Կոդ', 'Խանութ', 'Հասցե', 'Մենեջեր', 'Բեռ, կգ', 'Գումար, դրամ', 'Լայնություն', 'Երկայնություն']];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => tr.stops.forEach((s, si) => rows.push([
            truckLabel(t), i + 1, tr.depart, tr.return, si + 1, s.code, s.name, s.address, s.agent_name || s.agent_code,
            s.kg, Math.round(s.revenue / (s.share || 1)), s.lat, s.lon]))));
        const sum = [['Մեքենա', 'Երթեր', 'Կետեր', 'Բեռ, կգ', 'կմ', 'Լիտր', 'Վերադարձ']];
        plan.trucks.forEach(t => sum.push([truckLabel(t), t.trips.length, t.stops, t.kg, t.km, t.liters, t.return]));
        sum.push(['Ընդամենը', plan.summary.trips, plan.summary.stops, plan.summary.kg, plan.summary.km, plan.summary.liters, '']);
        if (plan.baseline) sum.push(['Եթե ըստ մենեջերների', plan.baseline.trips, '', '', plan.baseline.km, plan.baseline.liters, '']);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), 'Երթեր');
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(sum), 'Մեքենաներ');
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
        $('dpMapBox').addEventListener('toggle', () => { if ($('dpMapBox').open && state.data && state.data.plan) drawMap(); });
        setInterval(poll, 30 * 1000);       // poll() сам проверяет, прошло ли 5 минут (и после сна компьютера тоже)
        document.addEventListener('visibilitychange', poll);
        load(day);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

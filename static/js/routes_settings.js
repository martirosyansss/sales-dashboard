/* Настройки маршрутов /routes/settings: склад, машины, менеджеры, малый центр, нормы, сезон, калибровка.
   Контракт: GET/POST /api/routes/settings (docs/plans/stage-1-plan.md §10.2–10.3).
   Сохранение — одним запросом (сервер пишет всё или ничего), ошибки сервера — у полей.
   Строки из ERP (машины, менеджеры, группы клиентов) выводятся только через textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const ARM = { latMin: 38.8, latMax: 41.4, lonMin: 43.4, lonMax: 46.7 };
    const YEREVAN = [40.1792, 44.4991];
    // Страница на армянском (решение владельца №58); по-армянски после числа существительное — в единственном числе
    const NB = '\u00a0';
    const WD = [[1, 'Երկ', 'երկուշաբթի'], [2, 'Երք', 'երեքշաբթի'], [3, 'Չրք', 'չորեքշաբթի'], [4, 'Հնգ', 'հինգշաբթի'],
                [5, 'Ուրբ', 'ուրբաթ'], [6, 'Շբթ', 'շաբաթ'], [7, 'Կիր', 'կիրակի']];
    const MONTHS = ['հնվ', 'փտվ', 'մրտ', 'ապր', 'մյս', 'հնս', 'հլս', 'օգս', 'սեպ', 'հոկ', 'նոյ', 'դեկ'];
    const MONTHS_FULL = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    const FUEL = [['petrol', 'բենզին'], ['diesel', 'դիզել'], ['lpg', 'գազ']];
    const HOME_METHOD = { night: 'ըստ գիշերակացի վայրերի', morning: 'ըստ օրվա առաջին կետերի', first_point: 'ըստ օրվա առաջին կետերի' };
    // Ответы дашборда (вход, доступ) приходят по-русски — свой армянский текст по коду ответа
    // Кнопки масштаба Leaflet — подсказки по-армянски (по умолчанию «Zoom in» / «Zoom out»)
    const ZOOM_HY = { zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' };
    const AUTH_HY = { 401: 'Անհրաժեշտ է մուտք գործել համակարգ։', 403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։' };
    // 403 CSRF дашборда («сессия формы устарела») — не запрет доступа (как routes_learning.js и routes_garage.js)
    const CSRF_HY = 'Էջը հնացել է՝ թարմացրեք այն և կրկնեք։';
    const authText = (resp, data) => (resp.status === 403 && data && data.error === 'csrf' ? CSRF_HY : AUTH_HY[resp.status]);
    const SECTIONS = ['depot', 'trucks', 'fuel', 'managers', 'center', 'norms', 'season', 'calibration'];
    const ZONE_MAX = 200;   // точек границы малого центра — как store.CENTER_ZONE_VERTICES
    const RM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Нормы: ключ настроек → подпись простыми словами и пояснение в одну строку. min/max — подсказка браузеру,
    // окончательную проверку диапазонов делает сервер (§9), его ошибки показываются у поля.
    // place: 'fuel' — в «Обязательно заполнить», остальное — в «Можно не трогать»
    const NORMS = [
        { place: 'fuel', title: 'Մեկ լիտրի գինը, դրամ', items: [
            { key: 'fuel_price_diesel', label: 'Դիզել', min: 1, max: 10000, step: 1, nullable: true, hint: 'բեռնատարների և դիզելային մեքենաների համար' },
            { key: 'fuel_price_petrol', label: 'Բենզին', min: 1, max: 10000, step: 1, nullable: true, hint: 'մենեջերների բենզինով մեքենաների համար' },
            { key: 'fuel_price_lpg', label: 'Գազ', min: 1, max: 10000, step: 1, nullable: true, hint: 'պետք է միայն, եթե մենեջերներից որևէ մեկի մեքենան գազով է' },
        ] },
        { place: 'fuel', title: 'Մենեջերների մեքենաներ', items: [
            { key: 'manager_car_default_l_per_100km', label: 'Ծախսը, եթե նշված չէ, լ/100 կմ', min: 1, max: 40, step: 0.1,
                hint: 'այն մենեջերների համար, որոնց ծախսը «Ստուգեք» քայլում լրացված չէ' },
        ] },
        { title: 'Աշխատանքային օր', items: [
            { key: 'work_start', label: 'Սկիզբ', type: 'time' },
            { key: 'work_end', label: 'Ավարտ', type: 'time' },
            { key: 'workdays', kind: 'workdays', label: 'Աշխատանքային օրեր' },
        ] },
        // Парк машин (fleet-plan §2): рабочий день машины и разгрузка — для рейсов и «хватает ли машин»
        { title: 'Առաքման մեքենաներ', items: [
            { key: 'truck_work_start', label: 'Մեքենան մեկնում է', type: 'time' },
            { key: 'truck_work_end', label: 'Մեքենան վերադառնում է մինչև', type: 'time', hint: 'չհասցնող երթերի համար՝ «անհրաժեշտ է ևս մեկ մեքենա»' },
            { key: 'truck_overtime_end', label: 'Բացառիկ դեպքում մեքենան վերադառնում է ոչ ուշ, քան', type: 'time',
              hint: '«Առաքում» էջի «Տանել …-ից հետո» կոճակը — միայն բացառիկ օրերին' },
            // обед водителей в пути (ответ владельца №61): программа сама вставляет паузу в рейс
            { key: 'truck_lunch_min', label: 'Ճաշ ճանապարհին, րոպե', min: 0, max: 120, step: 1,
                hint: '0՝ առանց ճաշի։ Ծրագիրն ինքն է դադարը տեղադրում երթի մեջ, հաջորդ ժամանումները տեղաշարժվում են' },
            { key: 'truck_lunch_from', label: 'Ճաշը սկսվում է՝ ոչ շուտ, քան', type: 'time' },
            { key: 'truck_lunch_to', label: 'Ճաշը սկսվում է՝ ոչ ուշ, քան', type: 'time',
                hint: 'խանութում բեռնաթափելուց հետո կամ պահեստում՝ երթերի միջև․ եթե մինչև այս ժամը հարմար տեղ չի եղել, վարորդը կանգ է առնում ճանապարհին։ Ընդունման ժամին սպասելը հաշվվում է ճաշի մեջ' },
            // запас на рейс (ответ владельца №66): рейс укладывается в срок в q случаях из 100
            { key: 'dispatch_buffer_pct', label: 'Ժամանակի պաշար՝ երթը ժամանակին է 100-ից քանի դեպքում', min: 50, max: 95, step: 1,
                hint: 'Ծրագիրը յուրաքանչյուր երթի վերջում թողնում է ժամանակի պաշար, որ երթն ավարտվի պլանի ժամին այսքան դեպքում (պաշարը սովորում է ըստ GPS-ի)։ Կետերի ժամանումները՝ առանց պաշարի։ 50՝ առանց պաշարի։ Փոխելուց հետո պաշարը կգործի հաջորդ վերահաշվարկից' },
            { key: 'unload_min_per_stop', label: 'Բեռնաթափում մեկ կետում, րոպե', min: 0, max: 120, step: 1 },
            { key: 'unload_min_per_tonne', label: 'Եվս յուրաքանչյուր տոննայի համար, րոպե', min: 0, max: 120, step: 1 },
            { key: 'warehouse_load_fixed_min', label: 'Բեռնում պահեստում՝ երթի նախապատրաստում, րոպե', min: 0, max: 240, step: 1, nullable: true,
                hint: 'յուրաքանչյուր երթի համար, այդ թվում՝ երկրորդ և հաջորդ երթերի համար․ դատարկ՝ ժամանակը դեռ հայտնի չէ' },
            { key: 'warehouse_load_min_per_tonne', label: 'Բեռնում՝ լրացուցիչ յուրաքանչյուր տոննայի համար, րոպե', min: 0, max: 120, step: 0.5, nullable: true,
                hint: 'նշեք չափված ժամանակը․ 0՝ լրացուցիչ ժամանակ չկա' },
            { key: 'dispatch_ready_time', label: 'Վաղվա երթերը կազմել ոչ շուտ, քան', type: 'time',
                hint: 'մինչ այդ մենեջերները դեռ ընդունում են պատվերներ — «Առաքում» էջը կհիշեցնի այդ մասին' },
        ] },
        { title: 'Այցի տևողությունը, րոպե', items: [
            { key: 'visit_min_small', label: 'Փոքր խանութ', min: 1, max: 120, step: 0.5, nullable: true, auto: true, hint: 'դատարկ՝ վերցնում ենք GPS հետագծերում խանութների մոտ կանգառներից' },
            { key: 'visit_min_medium', label: 'Միջին խանութ', min: 1, max: 120, step: 0.5, nullable: true, auto: true },
            { key: 'visit_min_large', label: 'Խոշոր խանութ և ցանց', min: 1, max: 120, step: 0.5, nullable: true, auto: true },
        ] },
        { title: 'Խանութի չափը՝ ըստ միջին պատվերի', items: [
            { key: 'size_small_max_kg', label: 'Փոքր՝ մինչև, կգ', min: 1, max: 100000, step: 1 },
            { key: 'size_medium_max_kg', label: 'Միջին՝ մինչև, կգ', min: 1, max: 100000, step: 1, hint: 'ավելի ծանր՝ խոշոր' },
        ] },
        { title: 'Որքան պետք է բերի', items: [
            { key: 'min_day_revenue', label: 'Մենեջերի օրը՝ առնվազն, դրամ', min: 0, max: 100000000, step: 1000, hint: 'օրը, որը ձմռանը հավանաբար չի հավաքի այդքան, համարվում է թույլ' },
            { key: 'min_trip_revenue', label: 'Մեքենայի երթը՝ առնվազն, դրամ', min: 0, max: 100000000, step: 1000, hint: 'պակաս՝ մեքենան գնում է գրեթե դատարկ' },
        ] },
        // auto: пустое поле — «авто» (по GPS-трекам, без них — по умолчанию), см. autoText()
        { title: 'Ճանապարհներ և արագություն', items: [
            { key: 'detour_factor', label: 'Քանի անգամ է ճանապարհը երկար ուղիղ գծից', min: 1, max: 3, step: 0.01, nullable: true, auto: true, hint: 'դատարկ՝ վերցնում ենք GPS հետագծերից' },
            { key: 'speed_city_kmh', label: 'Արագությունը քաղաքում, կմ/ժ', min: 5, max: 120, step: 1, nullable: true, auto: true, hint: 'դատարկ՝ վերցնում ենք GPS հետագծերից' },
            { key: 'speed_region_kmh', label: 'Արագությունը մարզում, կմ/ժ', min: 5, max: 120, step: 1, nullable: true, auto: true, hint: 'դատարկ՝ վերցնում ենք GPS հետագծերից' },
            { key: 'city_center', kind: 'coord', label: 'Քաղաքի կենտրոն՝ լայնություն, երկայնություն', lat: 'city_center_lat', lon: 'city_center_lon' },
            { key: 'city_radius_km', label: 'Քաղաքի շառավիղ, կմ', min: 1, max: 50, step: 0.5, hint: 'ներսում՝ քաղաքային արագություն' },
        ] },
        // Этап 3 — как программа сравнивает варианты дней: во сколько драм «обходится» каждая неприятность
        { title: 'Ինչպես է ծրագիրն ընտրում օրերը', items: [
            { key: 'penalty_weak_day', label: 'Ձմռան թույլ օրվա պայմանական գինը, դրամ', min: 0, max: 10000000, step: 1000, hint: 'ավելի մեծ՝ ավելի ուժեղ է հավասարեցնում հասույթն ըստ օրերի' },
            { key: 'penalty_poor_trip', label: 'Գրեթե դատարկ երթի պայմանական գինը, դրամ', min: 0, max: 10000000, step: 500, hint: 'երթ, որի պատվերները պակաս են երթի նվազագույն արժեքից' },
            { key: 'penalty_overtime_per_min', label: 'Աշխատանքային օրից ավել մեկ րոպեի պայմանական գինը, դրամ', min: 0, max: 1000000, step: 50 },
            { key: 'penalty_change', label: 'Խանութն այլ օր տեղափոխելու պայմանական գինը, դրամ', min: 0, max: 1000000, step: 50, hint: 'ավելի մեծ՝ ավելի քիչ տեղափոխումներ մանր խնայողության համար' },
            { key: 'penalty_transfer', label: 'Խանութն այլ մենեջերի փոխանցելու պայմանական գինը, դրամ շաբաթում', min: 0, max: 1000000, step: 100, hint: 'փոխանցումը պետք է նկատելի օգուտ տա — ավելի մեծ՝ ավելի քիչ փոխանցումներ' },
            { key: 'transfer_radius_km', label: 'Ում կարելի է փոխանցել՝ մենեջերն ունի ավելի մոտ խանութ, կմ', min: 0.1, max: 20, step: 0.1, hint: 'գումարած 3 մենեջեր, որոնց տունն ամենամոտն է խանութին' },
            { key: 'truck_priority', label: 'Քանի անգամ են բեռնատարների ծախսերն ավելի կարևոր, քան մենեջերների վառելիքը', min: 1, max: 10, step: 0.1 },
            { key: 'fuel_price_fallback', label: 'Վառելիքի գինը, եթե «Վառելիքի գներ» բաժնում դատարկ է, դրամ/լ', min: 1, max: 10000, step: 1, hint: 'միայն տարբերակները համեմատելու համար' },
            { key: 'optimizer_seconds_per_manager', label: 'Հաշվարկի ժամանակը մեկ մենեջերի համար՝ առավելագույնը, վայրկյան', min: 1, max: 120, step: 1 },
            { key: 'abc_a_share', label: 'Խոշոր խանութներ՝ հասույթի բաժին', min: 0.05, max: 0.95, step: 0.05, hint: 'ամենախոշորները միասին տալիս են այս բաժինը' },
            { key: 'abc_b_share', label: 'Միջին խանութներ՝ հասույթի հաջորդ բաժինը', min: 0.05, max: 0.95, step: 0.05, hint: 'մնացածը փոքր են․ ցանկացած խանութ այցելվում է առնվազն շաբաթը մեկ անգամ' },
            { key: 'freq_safety', label: 'Այցերի պաշար', min: 0.5, max: 3, step: 0.1, hint: 'այցերը շաբաթում՝ ոչ պակաս, քան պատվերները շաբաթում × պաշար' },
        ] },
        // §15 — статус магазина по давности последнего заказа (ответ владельца №27)
        { title: 'Ովքեր են դադարել գնել', items: [
            { key: 'status_new_days', label: 'Նոր խանութ՝ առաջին պատվերը վերջին …, օր', min: 1, max: 365, step: 1, hint: 'նոր խանութները «դադարել է գնել» չեն համարվում' },
            { key: 'dormant_min_days', label: 'Դադարել է գնել՝ չի պատվիրում ավելի, քան …, օր', min: 1, max: 365, step: 1, hint: 'այդպիսիներին՝ այց ամեն շաբաթ, որ փորձենք վերադարձնել' },
            { key: 'dormant_mult', label: 'Դադարել է գնել՝ քանի անգամ ավելի երկար, քան սովորաբար', min: 1, max: 20, step: 0.5 },
            { key: 'lost_min_days', label: 'Վաղուց չի գնում՝ ավելի, քան …, օր', min: 1, max: 730, step: 1, hint: 'կամ վերջին տարում ոչ մի պատվեր՝ ծրագիրը կառաջարկի հանել երթուղուց' },
            { key: 'lost_mult', label: 'Վաղուց չի գնում՝ քանի անգամ ավելի երկար, քան սովորաբար', min: 1, max: 50, step: 0.5 },
        ] },
    ];
    // def — «авто» без калибровки по GPS (как ROAD_NORMS и VISIT_NORMS в route_optimizer/evaluate.py)
    const CALIB = [
        { key: 'detour_factor', label: 'Ճանապարհների ոլորունություն', d: 2, unit: '', def: 1.3 },
        { key: 'speed_city_kmh', label: 'Արագությունը քաղաքում', d: 1, unit: NB + 'կմ/ժ', def: 25 },
        { key: 'speed_region_kmh', label: 'Արագությունը մարզում', d: 1, unit: NB + 'կմ/ժ', def: 45 },
        { key: 'visit_min_small', label: 'Այց՝ փոքր խանութ', d: 1, unit: NB + 'րոպե', def: 7 },
        { key: 'visit_min_medium', label: 'Այց՝ միջին խանութ', d: 1, unit: NB + 'րոպե', def: 10 },
        { key: 'visit_min_large', label: 'Այց՝ խոշոր խանութ', d: 1, unit: NB + 'րոպե', def: 20 },
    ];

    const state = {
        data: null, initial: '', dirty: false, saving: false,
        map: null, marker: null,
        zone: [], zoneMap: null, zoneLayer: null,   // граница малого центра: [[широта, долгота], …] по обходу
        fields: new Map(),              // ключ ошибки сервера → {el, errEl, label}
        manual: [],                     // ручные машины формы: {key, car_code, name, van_agent_id, …}
        season: { mode: 'auto', low: new Set(), peak: new Set() },
    };

    // ---------- Утилиты ----------
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const round = (v, d) => { const k = 10 ** d; return Math.round(v * k) / k; };
    const inArmenia = (lat, lon) => lat >= ARM.latMin && lat <= ARM.latMax && lon >= ARM.lonMin && lon <= ARM.lonMax;
    const fmtCoord = (lat, lon) => (num(lat) === null || num(lon) === null) ? '' : Number(lat).toFixed(5) + ', ' + Number(lon).toFixed(5);

    // Узел DOM: строки-дети становятся текстом (никакого innerHTML с данными)
    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else if (k === 'on') Object.entries(v).forEach(([ev, fn]) => el.addEventListener(ev, fn));
            else if (k === 'dataset') Object.assign(el.dataset, v);
            else if (typeof v === 'boolean') el[k] = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => {
            if (c === null || c === undefined || c === false) return;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        });
        return el;
    }
    const icon = (cls) => h('i', { class: 'fas ' + cls, 'aria-hidden': 'true' });

    // Липкое меню дашборда переносится на 2–3 строки — якоря разделов учитывают его высоту
    function syncNavOffset() {
        const nav = document.querySelector('.navbar.sticky-top');
        $('rtSettings').style.setProperty('--rt-nav-h', (nav ? Math.ceil(nav.getBoundingClientRect().height) : 0) + 'px');
    }

    function announce(msg) {
        const el = $('rsStatus');
        el.textContent = '';
        setTimeout(() => { el.textContent = msg; }, 40);
    }

    // «40.1792, 44.4991» из Google Maps, «40,1792 44,4991», «40.1792;44.4991». Пусто → {empty}.
    function parseCoord(text) {
        const s = String(text || '').trim();
        if (!s) return { empty: true };
        const parts = s.match(/-?\d+(?:[.,]\d+)?/g) || [];
        if (parts.length !== 2) return { error: 'Անհրաժեշտ են երկու թիվ՝ լայնություն, երկայնություն — օրինակ՝ 40.1792, 44.4991' };
        let [a, b] = parts.map(x => parseFloat(x.replace(',', '.')));
        let swapped = false;
        if (!inArmenia(a, b) && inArmenia(b, a)) { [a, b] = [b, a]; swapped = true; }
        if (!inArmenia(a, b)) return { error: 'Կետը Հայաստանից դուրս է՝ լայնությունը պետք է լինի 38.8-ից մինչև 41.4, երկայնությունը՝ 43.4-ից մինչև 46.7' };
        return { lat: round(a, 6), lon: round(b, 6), swapped };
    }

    function normTime(v) {
        const m = /^\s*(\d{1,2})\s*[:.,]?\s*(\d{2})\s*$/.exec(String(v || ''));
        return (m && +m[1] <= 23 && +m[2] <= 59) ? m[1].padStart(2, '0') + ':' + m[2] : String(v || '').trim();
    }

    // Число из поля: {value} или {error}. Пустое — null, если поле необязательное.
    function readNum(input, nullable) {
        if (input.validity && input.validity.badInput) return { error: 'Գրեք թիվ' };
        const raw = String(input.value).trim().replace(',', '.');
        if (raw === '') return nullable ? { value: null } : { error: 'Լրացրեք դաշտը' };
        const v = Number(raw);
        return Number.isFinite(v) ? { value: v } : { error: 'Գրեք թիվ' };
    }

    // ---------- Регистр полей для ошибок сервера ----------
    // Путь ошибки может прийти как «trucks.0.capacity_kg», «trucks[0].capacity_kg»,
    // по коду/ID («managers.3144.home_lat») или с/без префикса «settings.».
    const normKey = (k) => String(k).replace(/\[([^\]]*)\]/g, '.$1').replace(/^\.+/, '');
    function reg(keys, el, errEl, label) {
        const entry = { el, errEl, label };
        keys.forEach(k => state.fields.set(normKey(k), entry));
        const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        if (errEl.id && !ids.includes(errEl.id)) el.setAttribute('aria-describedby', ids.concat(errEl.id).join(' '));
    }
    function findField(key) {
        const k = normKey(key);
        return state.fields.get(k) || state.fields.get(k.replace(/^settings\./, '')) || state.fields.get('settings.' + k) || null;
    }
    let errSeq = 0;
    const errNode = () => h('div', { class: 'rt-ferr', id: 'rsErr' + (++errSeq) });

    function clearErrors() {
        state.fields.forEach(f => {
            f.errEl.textContent = '';
            f.el.classList.remove('is-invalid');
            f.el.removeAttribute('aria-invalid');
        });
        $('rsSaveError').classList.add('d-none');
    }

    function showErrors(errors, fromClient) {
        clearErrors();
        const box = $('rsSaveErrorText');
        box.textContent = '';
        const list = h('ul');
        let count = 0;
        const seen = new Set();
        Object.entries(errors).forEach(([key, msg]) => {
            const text = String(msg);
            const f = findField(key);
            if (f) {
                const details = f.el.closest('details');
                if (details) details.open = true;
                f.errEl.textContent = f.errEl.textContent ? f.errEl.textContent + ' ' + text : text;
                f.el.classList.add('is-invalid');
                f.el.setAttribute('aria-invalid', 'true');
                if (seen.has(f)) return;   // два сообщения про одно поле — одна строка в сводке
                seen.add(f);
                list.append(h('li', {}, h('button', { type: 'button', class: 'rt-alert-link', on: { click: () => focusField(f.el) } },
                    (f.label ? f.label + '՝ ' : '') + text)));
            } else {
                list.append(h('li', { text: text }));
            }
            count++;
        });
        box.append(h('b', { text: (fromClient ? 'Ստուգեք դաշտերը — ' : 'Չի պահպանվել — ') + 'պետք է ուղղել՝ ' + count }), list);
        if ($('rsAuto').querySelector('.is-invalid')) $('rsAuto').open = true;   // ошибка в свёрнутом блоке — раскрываем его
        const alert = $('rsSaveError');
        alert.classList.remove('d-none');
        alert.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'start' });
        alert.focus({ preventScroll: true });
        announce('Կարգավորումները չեն պահպանվել՝ պետք է ուղղել ' + count);
    }

    function showSaveError(text) {
        const box = $('rsSaveErrorText');
        box.textContent = text;
        const alert = $('rsSaveError');
        alert.classList.remove('d-none');
        alert.focus({ preventScroll: true });
        alert.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'start' });
    }

    function focusField(el) {
        openFolds(el);
        // группа (рабочие дни, сети, месяцы) сама фокус не принимает — фокусируем первый элемент внутри
        const target = el.matches('input, select, textarea, button') ? el : (el.querySelector('input, select, button') || el);
        el.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'center' });
        target.focus({ preventScroll: true });
    }

    // Поле или раздел внутри свёрнутого блока — раскрыть все блоки над ним
    function openFolds(el) {
        for (let d = el.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
    }

    function flash(el) {
        el.classList.remove('rt-flash');
        void el.offsetWidth;   // перезапуск анимации
        el.classList.add('rt-flash');
    }

    // ---------- Загрузка ----------
    async function load(afterSave) {
        if (!afterSave) {
            $('rsLoading').classList.remove('d-none');
            $('rsLoadError').classList.add('d-none');
        }
        try {
            let resp;
            try {
                resp = await fetch('/api/routes/settings', { headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store' });
            } catch (e) {
                throw new Error('Սերվերի հետ կապ չկա։ Ստուգեք ցանցը և սեղմեք «Կրկնել»։');
            }
            let data = null;
            try { data = await resp.json(); } catch (e) { data = null; }
            if (authText(resp, data)) throw new Error(authText(resp, data));
            if (!data || typeof data !== 'object') throw new Error('Սերվերն անհասկանալի պատասխան տվեց (կոդ ' + resp.status + ')։');
            if (!resp.ok || data.success !== true) throw new Error(data.error || ('Սերվերի սխալ (կոդ ' + resp.status + ')։'));
            state.data = normalize(data);
            renderAll();
            $('rsForm').classList.remove('d-none');
            state.initial = snapshot();
            updateDirty();
            renderProgress();
            if (state.map) state.map.invalidateSize();
            if (state.zoneMap) { state.zoneMap.invalidateSize(); drawZone(true); }   // карта рисовалась в скрытой форме
            if (!afterSave) jumpToHash();
        } catch (e) {
            if (afterSave) {
                showSaveError('Կարգավորումները պահպանված են, բայց չհաջողվեց դրանք կրկին կարդալ՝ ' + e.message + ' Թարմացրեք էջը։');
            } else {
                $('rsLoadErrorText').textContent = 'Չհաջողվեց բեռնել կարգավորումները։ ' + e.message;
                $('rsLoadError').classList.remove('d-none');
                $('rsForm').classList.add('d-none');
            }
        } finally {
            $('rsLoading').classList.add('d-none');
        }
    }

    function normalize(d) {
        d.settings = (d.settings && typeof d.settings === 'object') ? d.settings : {};
        d.trucks = Array.isArray(d.trucks) ? d.trucks : [];
        d.expeditors = Array.isArray(d.expeditors) ? d.expeditors : [];
        d.managers = Array.isArray(d.managers) ? d.managers : [];
        d.customer_groups = Array.isArray(d.customer_groups) ? d.customer_groups : [];
        d.season = (d.season && typeof d.season === 'object') ? d.season : {};
        return d;
    }

    function jumpToHash() {
        const id = (location.hash || '').slice(1);
        if (!SECTIONS.includes(id)) return;
        const sec = $(id);
        openFolds(sec);
        requestAnimationFrame(() => {
            sec.scrollIntoView({ behavior: 'auto', block: 'start' });
            flash(sec.classList.contains('rt-card') ? sec : (sec.querySelector('.rt-group') || sec));
        });
    }

    function renderAll() {
        state.fields = new Map();
        renderDepot();
        renderTrucks();
        renderManagers();
        renderZone();
        renderNorms();
        renderSeason();
        renderCalib();
        updateSources();
        if ($('rsTrafficMode')) $('rsTrafficMode').value = state.data.settings.traffic_mode || 'gps';
        document.dispatchEvent(new CustomEvent('routes:settings', { detail: state.data }));
    }

    // ---------- 01 · Склад ----------
    function renderDepot() {
        const dp = state.data.depot;
        const inp = $('rsDepot');
        inp.value = dp ? fmtCoord(dp.lat, dp.lon) : '';
        reg(['depot', 'depot.lat', 'depot.lon'], inp, $('rsDepotErr'), 'Պահեստ');
        initDepotMap();
        syncDepot({ pan: true, strict: false });
    }

    function initDepotMap() {
        if (state.map) return;
        const el = $('rsDepotMap');
        if (typeof window.L === 'undefined') {
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Գրեք կոորդինատները աջ կողմի դաշտում։';
            return;
        }
        const map = L.map(el, { zoomSnap: 0.5, scrollWheelZoom: false, zoomControl: false });
        L.control.zoom(ZOOM_HY).addTo(map);
        RoutesBasemap.add(map);
        map.setView(YEREVAN, 11);
        map.on('click', (e) => placeDepot(e.latlng.lat, e.latlng.lng));
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
    }

    function depotMarker(lat, lon) {
        if (!state.map) return;
        if (!state.marker) {
            const icon = L.divIcon({
                className: 'rt-pin rt-pin-depot',
                html: '<span><i class="fas fa-warehouse" aria-hidden="true"></i></span>',
                iconSize: [30, 30], iconAnchor: [15, 15],
            });
            state.marker = L.marker([lat, lon], { icon, draggable: true, title: 'Պահեստ', keyboard: false }).addTo(state.map);
            state.marker.on('dragend', () => {
                const p = state.marker.getLatLng();
                placeDepot(p.lat, p.lng);
            });
        } else {
            state.marker.setLatLng([lat, lon]);
            if (!state.map.hasLayer(state.marker)) state.marker.addTo(state.map);
        }
    }

    function placeDepot(lat, lon) {
        const err = $('rsDepotErr'), inp = $('rsDepot');
        if (!inArmenia(lat, lon)) {
            err.textContent = 'Այս կետը Հայաստանից դուրս է — պահեստը պետք է լինի Հայաստանում։';
            if (state.marker) {
                const cur = parseCoord(inp.value);
                if (!cur.error && !cur.empty) state.marker.setLatLng([cur.lat, cur.lon]);
            }
            return;
        }
        inp.value = fmtCoord(lat, lon);
        syncDepot({ pan: false, strict: true });
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        announce('Պահեստը նշված է՝ ' + inp.value);
    }

    // strict — показывать ошибку формата (по «change»), иначе молча ждём, пока человек допечатает
    function syncDepot({ pan, strict }) {
        const inp = $('rsDepot'), note = $('rsDepotNote'), err = $('rsDepotErr');
        const p = parseCoord(inp.value);
        note.classList.remove('is-ok');
        if (p.empty) {
            err.textContent = '';
            inp.classList.remove('is-invalid');
            inp.removeAttribute('aria-invalid');
            note.textContent = 'Պահեստը նշված չէ — մեքենաների երթերը և դիզելը չեն հաշվվում։';
            if (state.marker && state.map) state.map.removeLayer(state.marker);
            return;
        }
        if (p.error) {
            if (strict) {
                err.textContent = p.error;
                inp.classList.add('is-invalid');
                inp.setAttribute('aria-invalid', 'true');
            }
            note.textContent = '';
            return;
        }
        err.textContent = '';
        inp.classList.remove('is-invalid');
        inp.removeAttribute('aria-invalid');
        note.textContent = p.swapped ? 'Լայնությունը և երկայնությունը շփոթված էին — տեղերը փոխեցինք։' : 'Կետը քարտեզում է։';
        note.classList.add('is-ok');
        depotMarker(p.lat, p.lon);
        if (pan && state.map) state.map.setView([p.lat, p.lon], Math.max(state.map.getZoom(), 14), { animate: !RM });
    }

    // ---------- 02 · Машины ----------
    // Машина закреплена за водителем, а не за менеджером (ответ владельца №29): вместо «чей менеджер» —
    // подсказка из ERP, сколько машина возит в день (по накладным за 3 месяца) и когда возила последний раз.
    // «Работает» у машины ERP: авто — решают накладные (возила за car_idle_days дней и не закрыта в ERP),
    // ручной выбор держится до «вернуть авто». Ручные машины (их нет в ERP: экспедиторы возят без машины
    // в накладных) добавляются здесь же и сохраняются общей кнопкой «Сохранить».
    const dateRu = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '')); return m ? m[3] + '.' + m[2] + '.' + m[1] : ''; };
    const codeKey = (c) => String(c || '').replace(/[\s-]+/g, '').toUpperCase();   // как store.code_key
    const MANUAL_CODE = /^[\p{L}\p{N}_\- ]{1,20}$/u;
    let manualSeq = 0;

    function erpHint(t) {
        const avg = num(t.erp_kg_day), top = num(t.erp_kg_day_max), days = num(t.erp_days);
        const load = avg === null ? h('span', { class: 'rs-erp-none', text: 'վերջին 3 ամսում ըստ ապրանքագրերի բեռ չի տարել' })
            : h('span', { class: 'rs-erp' }, 'սովորաբար օրական տանում է ≈' + NB + fmt(avg / 1000, 1) + NB + 'տ, առավելագույնը՝ '
                + fmt(top / 1000, 1) + NB + 'տ', h('small', { text: ' · ' + fmt(days) + NB + 'առաքման օր' }));
        return h('div', { class: 'rs-erp-box' }, load,
            h('span', { class: 'rs-erp-last', text: t.last_used ? 'վերջին անգամ ապրանքագրերում՝ ' + dateRu(t.last_used) : 'ապրանքագրերում չի եղել' }));
    }

    // Ручная машина: что о её экспедиторе известно из накладных (без машины, за 3 месяца)
    function vanHint(m) {
        const v = vanInfo(m.van_agent_id);
        if (!v) return h('span', { class: 'rs-erp-none', text: 'մեքենան ERP-ում չկա — «Պլան և փաստ» բաժնում երթերը չեն երևում, քանի դեռ առաքիչ ընտրված չէ' });
        return h('div', { class: 'rs-erp-box' },
            h('span', { class: 'rs-erp', text: 'բեռը տանում է առաքիչ ' + v.code + (v.name ? ' · ' + v.name : '') }),
            h('span', { class: 'rs-erp-last', text: num(v.docs) ? fmt(v.docs) + NB + 'ապրանքագիր առանց մեքենայի՝ վերջին 3 ամսում, վերջինը՝ '
                + dateRu(v.last_day) : 'վերջին 3 ամսում առանց մեքենայի ապրանքագրեր չեն եղել' }));
    }

    function vanInfo(id) {
        if (id === null || id === undefined) return null;
        const e = (state.data.expeditors || []).find(x => x.agent_id === id);
        if (e) return e;
        const t = state.data.trucks.find(x => x.manual && x.van && x.van.agent_id === id);
        return t ? t.van : { agent_id: id, code: String(id), name: '', docs: 0, last_day: null };
    }

    // «выключена, потому что не возила» — только пока «Работает» решает авто
    function idleText(t) {
        const tail = ' — անջատված է․ միացրեք, եթե մեքենան աշխատում է';
        if (t.erp_closed) return 'փակված է ERP-ում' + tail;
        if (!t.last_used) return 'ապրանքագրերում չի եղել' + tail;
        return dateRu(t.last_used) + '-ից բեռ չի տարել' + tail;
    }

    // Износ по журналу гаража (№53) — только чтение: ремонт ֏/км на сегодня и какое значение идёт в расчёт. Поле
    // «Износ, драм/км» остаётся ручным: страница сохраняет только его, значение журнала в настройки не попадает.
    // Текст — по-армянски (раздел «Маршруты» — только армянский, №58).
    function garageNote(t) {
        const g = t.garage, used = t.wear_source;
        // ручное задано, а по журналу у машины есть средняя — видна и она (в расчёт не идёт)
        const unused = used === 'manual' && t.garage_prior
            ? '; ' + priorText(t.garage_prior) + ' (չի կիրառվում, քանի որ վերևում արժեք կա)' : '';
        // своей цены журнала нет, поле выше пусто — в расчёте средняя модели (или парка) по журналу
        const inCalc = used === 'garage' ? 'հաշվարկում է այս արժեքը'
            : used === 'garage_avg' ? 'հաշվարկում է ' + priorText(t.garage_prior) + ', քանի որ վերևի դաշտը դատարկ է'
            : used === 'manual' ? 'հաշվարկում է վերևի դաշտի արժեքը' + unused : 'մաշվածքը հաշվարկում չի մասնակցում';
        let text;
        if (!g) text = 'Ավտոտնակի գրառումներ չկան — ' + inCalc;
        else if (g.status === 'ready' && g.blend) {
            // своя цена, сглаженная к средней модели (или парка): видно все три числа
            text = 'Ըստ ավտոտնակի գրառումների՝ ' + fmt(g.price, 1) + ' ֏/կմ (սեփականը՝ ' + fmt(g.own, 1) + ', '
                + (g.blend === 'model' ? g.model + '-ի միջինը՝ ' : 'ավտոպարկի միջինը՝ ') + fmt(g.model_price, 1) + '; '
                + fmt(g.months) + ' ամիս) — ' + inCalc;
        } else if (g.status === 'ready') text = 'Ըստ ավտոտնակի գրառումների՝ ' + fmt(g.price, 1) + ' ֏/կմ (' + fmt(g.months) + ' ամիս) — ' + inCalc;
        else if (g.status === 'accumulating') text = 'Ավտոտնակի գրառումներ․ կուտակվում է՝ ' + fmt(g.months) + ' / ' + fmt(g.ready_months) + ' ամիս — ' + inCalc;
        else if (g.status === 'low_km') text = 'Ավտոտնակի գրառումներ․ քիչ կմ (' + fmt(g.km) + ' կմ) — ' + inCalc;
        else if (g.status === 'no_repairs') text = 'Ավտոտնակի գրառումներ․ վերանորոգումներ գրանցված չեն — '
            + (used === 'manual' ? 'հաշվարկում է ձեռքով արժեքը' + unused : inCalc);
        else text = 'Ավտոտնակի գրառումներ․ վերջին 12 ամսում վազք չկա — ' + inCalc;
        return h('span', { class: 'rs-garage' + (used === 'garage' || used === 'garage_avg' ? ' is-used' : ''), text });
    }

    function priorText(p) {
        return (p.scope === 'model' ? 'մոդելի միջինը' + (p.model ? ' (' + p.model + ')' : '') : 'ավտոպարկի միջինը')
            + '՝ ' + fmt(p.price, 1) + ' ֏/կմ';
    }

    const LOAD_COSTS = [
        ['fuel_empty_l_per_100km', 'Դատարկ, լ/100 կմ', 1, 80],
        ['fuel_full_l_per_100km', 'Լրիվ բեռնված, լ/100 կմ', 1, 80],
        ['wear_amd_per_km', 'Մաշվածք, ֏/կմ', 0, 1000000],
        ['wear_load_amd_per_km', 'Հավելում լրիվ բեռնվածության դեպքում, ֏/կմ', 0, 1000000],
    ];

    function truckRow(t, i) {
        const manual = !!t.manual;
        const code = String(t.car_code ?? '');
        const who = 'մեքենա ' + code;
        const capE = errNode(), fuelE = errNode(), actE = errNode();
        const cap = h('input', { class: 'rt-input', type: 'number', inputmode: 'decimal', min: 0.1, max: 30, step: 0.1,
            value: num(t.capacity_kg) === null ? '' : String(round(num(t.capacity_kg) / 1000, 3)),
            placeholder: '—', 'aria-label': 'Տոննաժ, տոննա — ' + who, dataset: { f: 'cap' } });
        const fuel = h('input', { class: 'rt-input', type: 'number', inputmode: 'decimal', min: 1, max: 80, step: 0.1,
            value: num(t.fuel_l_per_100km) === null ? '' : String(t.fuel_l_per_100km),
            placeholder: '—', 'aria-label': 'Ծախս, լիտր 100 կմ-ի վրա — ' + who, dataset: { f: 'fuel' } });
        const costFields = LOAD_COSTS.map(([key, label, min, max]) => {
            const input = h('input', { class: 'rt-input', type: 'number', inputmode: 'decimal', min, max, step: 0.1,
                value: num(t[key]) === null ? '' : String(t[key]), placeholder: 'նշված չէ',
                'aria-label': label + ' — ' + who, dataset: { f: key } });
            const error = errNode();
            const prefix = manual ? ['manual_trucks.' + code] : ['trucks.' + i, 'trucks.' + code];
            reg(prefix.map(p => p + '.' + key), input, error, 'Մեքենա ' + code + ', ' + label);
            return h('label', { class: 'rs-load-field' }, h('span', { text: label }), input, error,
                key === 'wear_amd_per_km' ? garageNote(t) : null);
        });
        const costs = h('details', { class: 'rs-load-costs' },
            h('summary', { text: 'Բեռնվածություն և մաշվածք' + (t.wear_source === 'garage' ? ' · մաշվածքը՝ ըստ ավտոտնակի գրառումների'
                : t.wear_source === 'garage_avg' ? ' · մաշվածքը՝ ' + (t.garage_prior.scope === 'model' ? 'մոդելի' : 'ավտոպարկի') + ' միջինից' : '') }),
            h('div', { class: 'rs-load-fields' }, ...costFields),
            h('p', { class: 'rt-muted', text: 'Դատարկ և լրիվ բեռնված՝ ըստ այս մեքենայի չափումների։ Մաշվածքի հավելումը համեմատական է բեռնվածության բաժնի քառակուսուն։ Եթե դաշտերը լրացված չեն՝ բեռի ազդեցությունը կարգավորված չէ։' }));
        const wasManual = manual || t.active_source === 'manual', autoOn = t.auto_active === true;
        const active = h('input', { type: 'checkbox', checked: t.active !== false, 'aria-label': 'Մեքենան աշխատում է — ' + who,
            dataset: { f: 'active', mode: wasManual ? 'manual' : 'auto' } });
        const actSrc = h('span', { class: 'rt-inc-src' });
        const idle = h('span', { class: 'rs-idle', role: 'note' });
        const setMode = (mode) => {
            active.dataset.mode = mode;
            actSrc.textContent = '';
            // авто и давно не возила — выключена; владелец включил сам — мягкое напоминание, не выключаем
            const offByAuto = mode === 'auto' && !autoOn && !manual;
            const stale = mode === 'manual' && active.checked && !autoOn && !manual;
            idle.textContent = offByAuto ? idleText(t) : (stale ? 'ապրանքագրերում վաղուց չկա — ստուգեք, արդյոք մեքենան աշխատում է' : '');
            idle.classList.toggle('is-soft', stale);
            idle.hidden = !idle.textContent;
            if (manual) return;
            if (mode === 'auto') {
                actSrc.append(h('span', { class: 'rt-badge', text: 'ավտոմատ',
                    title: autoOn ? 'Ավտոմատ՝ վերջին ' + (state.data.car_idle_days || 60) + ' օրում բեռ է տարել ըստ ապրանքագրերի — աշխատում է'
                        : 'Ավտոմատ՝ ըստ ապրանքագրերի վաղուց բեռ չի տարել կամ փակված է ERP-ում — անջատված է' }));
                return;
            }
            const back = h('button', { type: 'button', class: 'rt-hintbtn', 'aria-label': 'Վերադարձնել ավտոմատ — ' + who,
                title: 'Հանել ձեռքով ընտրությունը՝ աշխատում է, եթե վերջին ' + (state.data.car_idle_days || 60) + ' օրում բեռ է տարել ըստ ապրանքագրերի' }, 'վերադարձնել ավտոմատ');
            back.addEventListener('click', () => {
                active.checked = autoOn;
                setMode('auto');
                updateDirty();
                renderProgress();
                active.focus();
                announce(code + '՝ «Աշխատում է» — կրկին ավտոմատ');
            });
            actSrc.append(back);
        };
        setMode(active.dataset.mode);
        // галочку вернули к авто-значению у машины, которая была «авто», — она и остаётся «авто»
        active.addEventListener('change', () => setMode(manual || wasManual || active.checked !== autoOn ? 'manual' : 'auto'));

        // «В центр» (№39–41): авто — по названию (машины JAC), ручной выбор — «да»/«нет»
        const autoCenter = manual ? /JAC/i.test(t.name || '') : t.auto_center_ok === true;
        const centerMode = t.center_mode || (t.center_ok_source === 'manual' ? (t.center_ok ? 'yes' : 'no') : 'auto');
        const centerE = errNode();
        const center = h('select', { class: 'rt-select rs-center-sel', 'aria-label': 'Կարող է մտնել փոքր կենտրոն — ' + who,
            title: 'Ավտոմատ՝ փոքր կենտրոն մտնում են JAC մեքենաները (ըստ մեքենայի անվանման)', dataset: { f: 'center', auto: autoCenter ? '1' : '0' } },
            h('option', { value: 'auto', text: 'ավտոմատ՝ ' + (autoCenter ? 'այո' : 'ոչ'), selected: centerMode === 'auto' }),
            h('option', { value: 'yes', text: 'այո', selected: centerMode === 'yes' }),
            h('option', { value: 'no', text: 'ոչ', selected: centerMode === 'no' }));
        const nameCell = h('td', { class: 'rt-cell-name' },
            h('span', { class: 'n', text: code || '—' }),
            h('span', { class: 'c', text: t.name || '' }),
            t.erp_closed ? h('span', { class: 'rt-badge b-warn mt-1', text: 'փակված է ERP-ում' }) : null,
            manual ? h('span', { class: 'rt-badge b-manual mt-1', text: 'ավելացված է ձեռքով' }) : null);
        const tr = h('tr', { class: t.erp_closed ? 'is-closed' : null, dataset: manual ? { mk: t.key } : { i: String(i) } },
            nameCell,
            h('td', { class: 'w-num w-half', dataset: { label: 'Տոննաժ, տ' } }, cap, capE),
            h('td', { class: 'w-num w-half', dataset: { label: 'Ծախս, լ/100 կմ' } }, fuel, fuelE, costs),
            h('td', { class: 'rs-erp-cell', dataset: { label: manual ? 'Առաքիչ' : 'Ըստ ERP ապրանքագրերի' } }, manual ? vanHint(t) : erpHint(t), idle),
            h('td', { class: 'w-sel', dataset: { label: 'Կենտրոն' } }, center, centerE),
            h('td', { class: 'w-chk w-inc', dataset: { label: 'Աշխատում է' } }, active, actSrc, actE));
        if (manual) {
            const edit = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Փոփոխել — ' + who },
                icon('fa-pen'), 'Փոփոխել');
            const del = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Ջնջել — ' + who },
                icon('fa-trash-can'), 'Ջնջել');
            edit.addEventListener('click', () => openManualForm(t.key));
            del.addEventListener('click', () => removeManual(t.key));
            nameCell.append(h('span', { class: 'rs-mt-acts' }, edit, del));
            const keys = (f) => ['manual_trucks.' + code + '.' + f];
            reg(keys('capacity_kg'), cap, capE, 'Մեքենա ' + code + ', տոննաժ');
            reg(keys('fuel_l_per_100km'), fuel, fuelE, 'Մեքենա ' + code + ', ծախս');
            reg(keys('active'), active, actE, 'Մեքենա ' + code);
            reg(keys('center_ok'), center, centerE, 'Մեքենա ' + code + ', կենտրոն');
            reg(keys('car_code').concat(keys('name'), keys('van_agent_id'), ['manual_trucks.' + code]), edit, actE, 'Մեքենա ' + code);
        } else {
            const keys = (f) => ['trucks.' + i + '.' + f, 'trucks.' + code + '.' + f];
            reg(keys('capacity_kg').concat(['trucks.' + i, 'trucks.' + code]), cap, capE, 'Մեքենա ' + code + ', տոննաժ');
            reg(keys('fuel_l_per_100km'), fuel, fuelE, 'Մեքենա ' + code + ', ծախս');
            reg(keys('active').concat(keys('car_code')), active, actE, 'Մեքենա ' + code);
            reg(keys('center_ok'), center, centerE, 'Մեքենա ' + code + ', կենտրոն');
        }
        return tr;
    }

    function renderTrucks() {
        const box = $('rsTrucks');
        box.textContent = '';
        state.manual = state.data.trucks.filter(t => t.manual).map(t => Object.assign({}, t, { key: 'm' + (++manualSeq) }));
        const tbody = h('tbody', { id: 'rsTrucksBody' });
        state.data.trucks.forEach((t, i) => { if (!t.manual) tbody.append(truckRow(t, i)); });
        state.manual.forEach(m => tbody.append(truckRow(m)));
        const table = h('table', { class: 'rt-table' },
            h('caption', { class: 'rt-sr-only', text: 'Առաքման մեքենաներ՝ տոննաժ, ծախս և որքան է մեքենան տանում օրական ըստ ERP ապրանքագրերի' }),
            h('thead', {}, h('tr', {},
                h('th', { scope: 'col', text: 'Մեքենա' }),
                h('th', { scope: 'col', text: 'Տոննաժ, տ' }),
                h('th', { scope: 'col', text: 'Ծախս, լ/100 կմ' }),
                h('th', { scope: 'col', text: 'Ըստ ERP ապրանքագրերի' }),
                h('th', { scope: 'col', text: 'Կենտրոն', title: 'Կարող է մտնել փոքր կենտրոն' }),
                h('th', { scope: 'col', class: 'w-chk w-inc', text: 'Աշխատում է' }))),
            tbody);
        const addBtn = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', id: 'rsMtAdd', 'aria-expanded': 'false', 'aria-controls': 'rsMtForm' },
            icon('fa-plus'), 'Ավելացնել մեքենա ձեռքով');
        addBtn.addEventListener('click', () => openManualForm(null));
        box.append(
            h('div', { class: 'rt-alert is-info rs-mt-hint', id: 'rsMtHint' }, icon('fa-circle-info'), h('span', { class: 'rt-alert-text', id: 'rsMtHintText' }), addBtn),
            h('div', { class: 'rt-table-scroll' }, table),
            manualForm());
        renderManualHint();
    }

    // «Нет нужной машины?» — экспедиторы, которые возят без машины в накладных и ещё без ручной машины
    function renderManualHint() {
        const linked = new Set(state.manual.map(m => m.van_agent_id).filter(v => v !== null && v !== undefined));
        const free = (state.data.expeditors || []).filter(e => !linked.has(e.agent_id)).map(e => e.code);
        let text = 'Չե՞ք գտնում անհրաժեշտ մեքենան։ Եթե այն ERP-ում չկա, ավելացրեք ձեռքով։';
        if (free.length) {
            const list = free.slice(0, 3);
            const names = list.length === 1 ? list[0] : list.slice(0, -1).join(', ') + ' և ' + list[list.length - 1];
            text += ' Օրինակ՝ ապրանքագրերում առանց մեքենայի ' + (free.length === 1 ? 'բեռ է տանում առաքիչ ' + names : 'բեռ են տանում առաքիչներ ' + names + (free.length > 3 ? ' և ուրիշներ' : ''))
                + '։';
        }
        $('rsMtHintText').textContent = text;
    }

    // ---- Форма «Добавить машину вручную» / «Изменить» (внутри общей формы — поля не отслеживаются как изменения) ----
    function manualForm() {
        const f = (id, label, input, hint) => h('div', { class: 'rt-field' }, h('label', { for: id, text: label }), input,
            hint ? h('div', { class: 'rt-field-hint', text: hint }) : null);
        const nt = { noTrack: '1' };
        const code = h('input', { class: 'rt-input', id: 'rsMtCode', type: 'text', maxlength: 20, autocomplete: 'off', spellcheck: 'false', placeholder: 'օրինակ՝ 35 XY 123', dataset: nt });
        const name = h('input', { class: 'rt-input', id: 'rsMtName', type: 'text', maxlength: 60, autocomplete: 'off', placeholder: 'օրինակ՝ Գազել', dataset: nt });
        const cap = h('input', { class: 'rt-input', id: 'rsMtCap', type: 'number', inputmode: 'decimal', min: 0.1, max: 30, step: 0.1, placeholder: '—', dataset: nt });
        const fuel = h('input', { class: 'rt-input', id: 'rsMtFuel', type: 'number', inputmode: 'decimal', min: 1, max: 80, step: 0.1, placeholder: '—', dataset: nt });
        const van = h('select', { class: 'rt-select', id: 'rsMtVan', dataset: nt });
        const act = h('input', { type: 'checkbox', id: 'rsMtActive', checked: true, dataset: nt });
        const ok = h('button', { type: 'button', class: 'rt-btn rt-btn-primary rt-btn-sm', id: 'rsMtOk' }, icon('fa-check'), h('span', { text: 'Ավելացնել ցուցակում' }));
        const cancel = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', id: 'rsMtCancel' }, 'Չեղարկել');
        ok.addEventListener('click', applyManualForm);
        cancel.addEventListener('click', closeManualForm);
        const wrap = h('div', { class: 'rs-mt-form', id: 'rsMtForm', role: 'group', 'aria-labelledby': 'rsMtTitle', hidden: true },
            h('h4', { class: 'rs-mt-title', id: 'rsMtTitle', text: 'Նոր մեքենա' }),
            h('div', { class: 'rs-mt-grid' },
                f('rsMtCode', 'Համարանիշ', code, 'ինչպես մեքենայի վրա․ համարանիշը չպետք է համընկնի ERP-ի մեքենաների հետ'),
                f('rsMtName', 'Մակնիշ կամ անվանում', name),
                f('rsMtCap', 'Տոննաժ, տ', cap),
                f('rsMtFuel', 'Ծախս, լ/100 կմ', fuel),
                f('rsMtVan', 'Առաքիչ (կարելի է չնշել)', van, 'ERP-ում նրա առանց մեքենայի ապրանքագրերը ծրագիրը կհամարի այս մեքենայի երթեր'),
                h('label', { class: 'rs-mt-check' }, act, 'Մեքենան աշխատում է')),
            h('div', { class: 'rt-ferr', id: 'rsMtErr', role: 'alert' }),
            h('div', { class: 'rs-mt-btns' }, ok, cancel),
            h('p', { class: 'rt-field-hint mb-0', text: 'Մեքենան կավելանա ցուցակում։ Պահպանելու համար սեղմեք «Պահպանել» կոճակը էջի ներքևում։' }));
        wrap.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.preventDefault(); applyManualForm(); }
            if (e.key === 'Escape') { e.preventDefault(); closeManualForm(); }
        });
        return wrap;
    }

    function fillVanOptions(selected, editingKey) {
        const sel = $('rsMtVan');
        sel.textContent = '';
        sel.append(h('option', { value: '', text: '— առանց առաքիչի —' }));
        const list = (state.data.expeditors || []).slice();
        if (selected !== null && selected !== undefined && !list.some(e => e.agent_id === selected)) list.push(vanInfo(selected));
        list.forEach(e => {
            const other = state.manual.find(m => m.van_agent_id === e.agent_id && m.key !== editingKey);
            sel.append(h('option', { value: String(e.agent_id), selected: e.agent_id === selected, disabled: !!other,
                text: e.code + (e.name ? ' · ' + e.name : '') + (num(e.docs) ? ' · ' + fmt(e.docs) + NB + 'ապրանքագիր առանց մեքենայի' : '')
                    + (other ? ' (արդեն կցված է ' + other.car_code + ' մեքենային)' : '') }));
        });
    }

    function openManualForm(key) {
        const m = key ? state.manual.find(x => x.key === key) : null;
        const tr = m ? $('rsTrucksBody').querySelector('tr[data-mk="' + key + '"]') : null;
        const cur = (f) => tr ? tr.querySelector('[data-f="' + f + '"]') : null;
        const form = $('rsMtForm');
        form.dataset.key = key || '';
        $('rsMtTitle').textContent = m ? 'Մեքենա ' + m.car_code : 'Նոր մեքենա';
        $('rsMtOk').querySelector('span').textContent = m ? 'Կիրառել' : 'Ավելացնել ցուցակում';
        $('rsMtCode').value = m ? m.car_code : '';
        $('rsMtCode').disabled = !!m;
        $('rsMtCode').title = m ? 'Համարանիշը չի փոխվում — ջնջեք մեքենան և ավելացրեք նորից' : '';
        $('rsMtName').value = m ? (m.name || '') : '';
        $('rsMtCap').value = cur('cap') ? cur('cap').value : '';
        $('rsMtFuel').value = cur('fuel') ? cur('fuel').value : '';
        $('rsMtActive').checked = cur('active') ? cur('active').checked : true;
        fillVanOptions(m ? m.van_agent_id : null, key);
        $('rsMtErr').textContent = '';
        form.hidden = false;
        $('rsMtAdd').setAttribute('aria-expanded', 'true');
        form.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'nearest' });
        (m ? $('rsMtName') : $('rsMtCode')).focus({ preventScroll: true });
    }

    function closeManualForm() {
        $('rsMtForm').hidden = true;
        $('rsMtAdd').setAttribute('aria-expanded', 'false');
        $('rsMtAdd').focus();
    }

    function applyManualForm() {
        const key = $('rsMtForm').dataset.key || null;
        const err = (text, el) => { $('rsMtErr').textContent = text; if (el) el.focus(); };
        const code = String($('rsMtCode').value).trim().replace(/\s+/g, ' ');
        if (!key) {
            if (!MANUAL_CODE.test(code)) return err('Համարանիշ՝ մինչև 20 տառ և թիվ (կարելի է բացատ և գծիկ)։', $('rsMtCode'));
            if (state.data.trucks.some(t => !t.manual && codeKey(t.car_code) === codeKey(code))) return err(code + ' մեքենան արդեն կա ERP-ի ցուցակում — լրացրեք դրա տողը։', $('rsMtCode'));
            if (state.manual.some(m => codeKey(m.car_code) === codeKey(code))) return err('Այս համարանիշով մեքենան արդեն ավելացված է։', $('rsMtCode'));
        }
        const name = String($('rsMtName').value).trim().replace(/\s+/g, ' ');
        if (name.length > 60) return err('Անվանումը՝ առավելագույնը 60 նիշ։', $('rsMtName'));
        const cap = readNum($('rsMtCap'), true), fuel = readNum($('rsMtFuel'), true);
        if (cap.error || (cap.value !== null && (cap.value < 0.1 || cap.value > 30))) return err('Տոննաժը՝ 0,1-ից մինչև 30 տ։', $('rsMtCap'));
        if (fuel.error || (fuel.value !== null && (fuel.value < 1 || fuel.value > 80))) return err('Ծախսը՝ 1-ից մինչև 80 լ/100 կմ։', $('rsMtFuel'));
        const vanRaw = $('rsMtVan').value;
        const data = {
            name: name || null,
            capacity_kg: cap.value === null ? null : Math.round(cap.value * 1000),
            fuel_l_per_100km: fuel.value,
            active: $('rsMtActive').checked,
            van_agent_id: vanRaw === '' ? null : Number(vanRaw),
        };
        let m;
        if (key) {
            m = state.manual.find(x => x.key === key);
            Object.assign(m, data);
            const old = $('rsTrucksBody').querySelector('tr[data-mk="' + key + '"]');
            m.center_mode = old.querySelector('[data-f="center"]').value;
            const costValues = LOAD_COSTS.map(([f]) => old.querySelector('[data-f="' + f + '"]').value);
            const next = truckRow(m);
            LOAD_COSTS.forEach(([f], i) => { next.querySelector('[data-f="' + f + '"]').value = costValues[i]; });
            next.querySelector('details').open = old.querySelector('details').open;
            old.replaceWith(next);
        } else {
            m = Object.assign({ car_code: code, manual: true, active_source: 'manual', key: 'm' + (++manualSeq) }, data);
            state.manual.push(m);
            $('rsTrucksBody').append(truckRow(m));
        }
        $('rsMtForm').hidden = true;
        $('rsMtAdd').setAttribute('aria-expanded', 'false');
        renderManualHint();
        updateDirty();
        renderProgress();
        const row = $('rsTrucksBody').querySelector('tr[data-mk="' + m.key + '"]');
        flash(row);
        row.querySelector('button').focus({ preventScroll: true });
        row.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'nearest' });
        announce(m.car_code + ' մեքենան ' + (key ? 'փոփոխվեց' : 'ավելացվեց ցուցակում') + ' — սեղմեք «Պահպանել»');
    }

    function removeManual(key) {
        const i = state.manual.findIndex(x => x.key === key);
        if (i < 0) return;
        const [m] = state.manual.splice(i, 1);
        $('rsTrucksBody').querySelector('tr[data-mk="' + key + '"]').remove();
        if ($('rsMtForm').dataset.key === key) $('rsMtForm').hidden = true;
        renderManualHint();
        updateDirty();
        renderProgress();
        $('rsMtAdd').focus();
        announce(m.car_code + ' մեքենան հանվեց ցուցակից — սեղմեք «Պահպանել»');
    }

    // ---------- 03 · Менеджеры ----------
    function renderManagers() {
        const box = $('rsManagers');
        box.textContent = '';
        const list = state.data.managers;
        if (!list.length) {
            box.append(h('p', { class: 'rt-empty px-0', text: 'ERP-ում երթուղիներով մենեջերներ չկան։' }));
            return;
        }
        const defL = num(state.data.settings.manager_car_default_l_per_100km);
        const tbody = h('tbody');
        list.forEach((m, i) => {
            const name = m.name || ('Մենեջեր ' + m.agent_id);
            const incE = errNode(), homeE = errNode(), fuelE = errNode(), typeE = errNode();
            // «В расчёте»: авто — решает работа за 8 недель (сервер: inactive), ручной выбор держится
            // до «вернуть авто». data-mode: auto | manual — что уйдёт на сервер (см. collect()).
            const autoInc = m.inactive !== true, wasManual = m.included_source === 'manual';
            const inc = h('input', { type: 'checkbox', checked: m.included !== false, 'aria-label': 'Հաշվարկում — ' + name,
                dataset: { f: 'inc', mode: wasManual ? 'manual' : 'auto' } });
            const incSrc = h('span', { class: 'rt-inc-src' });
            const setIncMode = (mode) => {
                inc.dataset.mode = mode;
                incSrc.textContent = '';
                if (mode === 'auto') {
                    incSrc.append(h('span', { class: 'rt-badge', text: 'ավտոմատ',
                        title: autoInc ? 'Ավտոմատ՝ 8 շաբաթում կան պատվերներ կամ այցեր — հաշվարկում է'
                            : 'Ավտոմատ՝ 8 շաբաթում պատվերներ և այցեր չկան — հաշվարկում չէ' }));
                    return;
                }
                const back = h('button', { type: 'button', class: 'rt-hintbtn', 'aria-label': 'Վերադարձնել ավտոմատ — ' + name,
                    title: 'Հանել ձեռքով ընտրությունը՝ հաշվարկում է, եթե 8 շաբաթում կան պատվերներ կամ այցեր' }, 'վերադարձնել ավտոմատ');
                back.addEventListener('click', () => {
                    inc.checked = autoInc;
                    setIncMode('auto');
                    updateDirty();
                    inc.focus();
                    announce(name + '՝ «Հաշվարկում» — կրկին ավտոմատ');
                });
                incSrc.append(back);
            };
            setIncMode(inc.dataset.mode);
            // галочку вернули к авто-значению у менеджера, который был «авто», — он и остаётся «авто»
            inc.addEventListener('change', () => setIncMode(wasManual || inc.checked !== autoInc ? 'manual' : 'auto'));
            const home = m.home || {};
            const homeInp = h('input', { class: 'rt-input rt-num', type: 'text', inputmode: 'decimal', autocomplete: 'off', spellcheck: 'false',
                value: fmtCoord(home.lat, home.lon), placeholder: 'դատարկ՝ տունն ըստ GPS-ի',
                'aria-label': 'Տուն, լայնություն և երկայնություն — ' + name, dataset: { f: 'home' } });
            const sug = m.home_suggestion;
            const hasSug = !!sug && num(sug.lat) !== null && num(sug.lon) !== null;
            // Дом по GPS: «Верно» — закрепить найденную точку, «Поправить» — закрепить и исправить вручную
            const homeState = h('div', { class: 'rs-home', 'aria-live': 'polite' });
            const pinGps = () => {
                homeInp.value = fmtCoord(sug.lat, sug.lon);
                homeE.textContent = '';
                homeInp.classList.remove('is-invalid');
                homeInp.removeAttribute('aria-invalid');
                homeInp.dispatchEvent(new Event('input', { bubbles: true }));
                flash(homeInp);
            };
            const how = hasSug ? (HOME_METHOD[sug.method] || 'ըստ հետագծի') + (num(sug.days) !== null ? ', ' + fmt(sug.days) + NB + 'օր' : '') : '';
            const isGps = () => {
                const p = parseCoord(homeInp.value);
                return hasSug && !p.empty && !p.error && Math.abs(p.lat - num(sug.lat)) < 1e-4 && Math.abs(p.lon - num(sug.lon)) < 1e-4;
            };
            const smallBtn = (text, label, fn) => {
                const b = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': label + ' — ' + name }, text);
                b.addEventListener('click', fn);
                return b;
            };
            const renderHome = () => {
                homeState.textContent = '';
                const p = parseCoord(homeInp.value);
                if (p.empty && hasSug) {
                    homeState.append(h('span', { class: 'rt-badge b-gps', text: 'գտնվել է GPS-ով' }), h('span', { class: 'how', text: how }),
                        smallBtn('Ճիշտ է', 'Տունը ճիշտ է', () => { pinGps(); announce(name + '՝ GPS-ով գտնված տունն ամրացված է'); }),
                        smallBtn('Ուղղել', 'Ուղղել տունը', () => { pinGps(); homeInp.focus(); homeInp.select(); announce(name + '՝ ուղղեք տան կոորդինատները'); }));
                } else if (p.empty) {
                    homeState.append(h('span', { class: 'rt-badge b-warn', text: 'չի գտնվել' }), h('span', { class: 'how', text: 'գրեք տան լայնությունը և երկայնությունը' }));
                } else if (isGps()) {
                    homeState.append(h('span', { class: 'rt-badge b-ok', text: 'GPS-ով, ստուգված' }));
                } else {
                    homeState.append(h('span', { class: 'rt-badge b-manual', text: 'նշված է ձեռքով' }),
                        hasSug ? smallBtn('Վերցնել GPS-ից', 'Վերցնել տունը GPS-ից', () => { pinGps(); announce(name + '՝ տունը վերցվեց GPS-ից'); }) : null);
                }
            };
            homeInp.addEventListener('input', renderHome);
            renderHome();
            homeInp.addEventListener('change', () => checkCoordField(homeInp, homeE));
            const fuelL = h('input', { class: 'rt-input', type: 'number', inputmode: 'decimal', min: 1, max: 40, step: 0.1,
                value: num(m.car_fuel_l_per_100km) === null ? '' : String(m.car_fuel_l_per_100km),
                placeholder: defL !== null ? 'սովորաբար ' + fmt(defL, 1) : 'սովորական',
                'aria-label': 'Մեքենայի ծախս, լիտր 100 կմ-ի վրա — ' + name, dataset: { f: 'fuel' } });
            const typeOpts = FUEL.map(([v, t]) => h('option', { value: v, text: t, selected: m.car_fuel_type === v }));
            if (!FUEL.some(([v]) => v === m.car_fuel_type)) typeOpts.unshift(h('option', { value: '', text: '— նշված չէ —', selected: true }));
            const type = h('select', { class: 'rt-select', 'aria-label': 'Վառելիք — ' + name, dataset: { f: 'type' } }, typeOpts);
            tbody.append(h('tr', { dataset: { i: String(i) } },
                h('td', { class: 'w-chk w-inc', dataset: { label: 'Հաշվարկում' } }, inc, incSrc, incE),
                h('td', { class: 'rt-cell-name' }, h('span', { class: 'n', text: name }), h('span', { class: 'c', text: m.code || '' }),
                    m.inactive ? h('span', { class: 'rt-badge b-warn mt-1', text: '8 շաբաթ առանց աշխատանքի',
                        title: '8 շաբաթում պատվերներ և այցեր չկան — լռելյայն հաշվարկում չէ' }) : null),
                h('td', { class: 'w-coord', dataset: { label: 'Տուն՝ լայնություն, երկայնություն' } },
                    homeInp,
                    homeState,
                    homeE),
                h('td', { class: 'w-num w-half', style: 'min-width:150px', dataset: { label: 'Ծախս, լ/100 կմ' } }, fuelL, fuelE),
                h('td', { class: 'w-sel w-half', style: 'min-width:130px', dataset: { label: 'Վառելիք' } }, type, typeE)));
            const keys = (f) => ['managers.' + i + '.' + f, 'managers.' + m.agent_id + '.' + f];
            reg(keys('included').concat(keys('agent_id'), ['managers.' + i, 'managers.' + m.agent_id]), inc, incE, name);
            reg(keys('home_lat').concat(keys('home_lon'), keys('home')), homeInp, homeE, name + ', տուն');
            reg(keys('car_fuel_l_per_100km'), fuelL, fuelE, name + ', ծախս');
            reg(keys('car_fuel_type'), type, typeE, name + ', վառելիք');
        });
        const table = h('table', { class: 'rt-table' },
            h('caption', { class: 'rt-sr-only', text: 'Մենեջերներ՝ մասնակցություն հաշվարկին, տուն, մեքենա' }),
            h('thead', {}, h('tr', {},
                h('th', { scope: 'col', class: 'w-chk w-inc', text: 'Հաշվարկում' }),
                h('th', { scope: 'col', text: 'Մենեջեր' }),
                h('th', { scope: 'col', text: 'Տուն՝ լայնություն, երկայնություն' }),
                h('th', { scope: 'col', text: 'Ծախս, լ/100 կմ' }),
                h('th', { scope: 'col', text: 'Վառելիք' }))),
            tbody);
        box.append(h('div', { class: 'rt-table-scroll' }, table));
    }

    function checkCoordField(inp, errEl) {
        const p = parseCoord(inp.value);
        const bad = !!p.error;
        errEl.textContent = bad ? p.error : '';
        inp.classList.toggle('is-invalid', bad);
        if (bad) inp.setAttribute('aria-invalid', 'true'); else inp.removeAttribute('aria-invalid');
        if (!bad && !p.empty) inp.value = fmtCoord(p.lat, p.lon);
    }

    // ---------- Малый центр: граница на карте (№39–41) ----------
    // Вершины тянутся мышью; щелчок по карте добавляет вершину на ближайшую сторону; двойной щелчок по вершине —
    // убирает её (меньше трёх нельзя). Сохраняется общей кнопкой вместе с остальными настройками (settings.center_zone).
    function renderZone() {
        const z = state.data.settings.center_zone;
        state.zone = Array.isArray(z) ? z.filter(p => Array.isArray(p) && p.length === 2).map(p => [Number(p[0]), Number(p[1])]) : [];
        reg(['settings.center_zone'], $('rsCenterMap'), $('rsCenterErr'), 'Փոքր կենտրոնի սահման');
        initZoneMap();
        drawZone(true);
    }

    function initZoneMap() {
        if (state.zoneMap) return;
        const el = $('rsCenterMap');
        if (typeof window.L === 'undefined') {
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ) — կենտրոնի սահմանը մնում է նախկինը։';
            return;
        }
        const map = L.map(el, { zoomSnap: 0.5, scrollWheelZoom: false, doubleClickZoom: false, zoomControl: false });
        L.control.zoom(ZOOM_HY).addTo(map);
        RoutesBasemap.add(map);
        map.setView(YEREVAN, 14);
        map.on('click', (e) => addZonePoint(e.latlng.lat, e.latlng.lng));
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.zoneMap = map;
        state.zoneLayer = L.layerGroup().addTo(map);
    }

    function drawZone(fit) {
        const n = state.zone.length;
        $('rsCenterNote').textContent = 'Սահմանի կետեր՝ ' + n + (n <= 3 ? ' — երեքից պակաս չի կարող լինել' : '') + '։';
        if (!state.zoneMap) return;
        state.zoneLayer.clearLayers();
        const poly = L.polygon(state.zone, { color: '#397be9', weight: 3, fillOpacity: 0.12, interactive: false }).addTo(state.zoneLayer);
        const icon = L.divIcon({ className: '', html: '<span class="rs-zone-pt"></span>', iconSize: [14, 14], iconAnchor: [7, 7] });
        state.zone.forEach((p, i) => {
            const mk = L.marker(p, { icon, draggable: true, keyboard: false, title: 'Կետ ' + (i + 1) + ' — քաշեք՝ տեղաշարժելու համար, կրկնակի սեղմեք՝ հեռացնելու համար' })
                .addTo(state.zoneLayer);
            mk.on('drag', (e) => { const ll = e.target.getLatLng(); state.zone[i] = [ll.lat, ll.lng]; poly.setLatLngs(state.zone); });
            mk.on('dragend', (e) => {
                const ll = e.target.getLatLng();
                state.zone[i] = inArmenia(ll.lat, ll.lng) ? [round(ll.lat, 6), round(ll.lng, 6)] : p;
                drawZone(false);
                zoneChanged('Կետ ' + (i + 1) + '-ը տեղաշարժվեց');
            });
            mk.on('dblclick', () => {
                if (state.zone.length <= 3) { announce('Սահմանը պետք է ունենա առնվազն երեք կետ'); return; }
                state.zone.splice(i, 1);
                drawZone(false);
                zoneChanged('Կետ ' + (i + 1) + '-ը հեռացվեց');
            });
        });
        if (fit && n) state.zoneMap.fitBounds(state.zone, { padding: [24, 24], animate: false });
    }

    // Щелчок по карте — новая вершина на ближайшей стороне (расстояние до отрезка на плоскости, долгота × cos широты)
    function addZonePoint(lat, lon) {
        const n = state.zone.length;
        if (!inArmenia(lat, lon)) return;
        if (n >= ZONE_MAX) { announce('Սահմանը կարող է ունենալ առավելագույնը ' + ZONE_MAX + ' կետ'); return; }
        const k = Math.cos(lat * Math.PI / 180);
        const xy = (p) => [p[1] * k, p[0]];
        const c = xy([lat, lon]);
        const dist = (a, b) => {
            const dx = b[0] - a[0], dy = b[1] - a[1], len = dx * dx + dy * dy;
            const t = len ? Math.max(0, Math.min(1, ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / len)) : 0;
            return Math.hypot(c[0] - a[0] - t * dx, c[1] - a[1] - t * dy);
        };
        let best = n - 1, bestD = Infinity;
        for (let i = 0; i < n; i++) {
            const d = dist(xy(state.zone[i]), xy(state.zone[(i + 1) % n]));
            if (d < bestD) { bestD = d; best = i; }
        }
        state.zone.splice(best + 1, 0, [round(lat, 6), round(lon, 6)]);
        drawZone(false);
        zoneChanged('Կետն ավելացվեց');
    }

    function zoneChanged(msg) {
        $('rsCenterErr').textContent = '';
        $('rsCenterMap').classList.remove('is-invalid');
        updateDirty();
        if (msg) announce(msg + ' — սեղմեք «Պահպանել»');
    }

    // ---------- 04 · Нормы ----------
    function renderNorms() {
        const box = $('rsNorms'), fuel = $('rsFuel');
        box.textContent = '';
        fuel.textContent = '';
        const s = state.data.settings;
        NORMS.forEach(g => {
            const group = h('div', { class: 'rt-group' }, h('h3', { text: g.title }));
            g.items.forEach(it => group.append(normField(it, s)));
            (g.place === 'fuel' ? fuel : box).append(group);
        });
        box.append(chainsGroup(s));
    }

    function normField(it, s) {
        const id = 'rsN_' + it.key;
        const err = errNode();
        if (it.kind === 'workdays') {
            const sel = new Set((Array.isArray(s.workdays) ? s.workdays : []).map(Number));
            const wrap = h('div', { class: 'rt-wd', role: 'group', 'aria-labelledby': id + '_l', id: id });
            WD.forEach(([n, short, full]) => wrap.append(h('label', { title: full },
                h('input', { type: 'checkbox', value: String(n), checked: sel.has(n), 'aria-label': full, dataset: { wd: '1' } }), short)));
            reg(['settings.workdays'], wrap, err, 'Աշխատանքային օրեր');
            return h('div', { class: 'rt-field' }, h('span', { class: 'rt-field-label', id: id + '_l', text: it.label }), wrap, err);
        }
        if (it.kind === 'coord') {
            const inp = h('input', { class: 'rt-input rt-num', type: 'text', id, inputmode: 'decimal', autocomplete: 'off', spellcheck: 'false',
                value: fmtCoord(s[it.lat], s[it.lon]), placeholder: '40.17920, 44.49910' });
            inp.addEventListener('change', () => checkCoordField(inp, err));
            reg(['settings.' + it.lat, 'settings.' + it.lon, 'settings.' + it.key], inp, err, it.label);
            return h('div', { class: 'rt-field' }, h('label', { for: id, text: it.label }), inp, err);
        }
        const isTime = it.type === 'time';
        const inp = h('input', {
            class: 'rt-input' + (isTime ? ' rt-num' : ''), id, type: isTime ? 'text' : 'number',
            inputmode: isTime ? 'numeric' : 'decimal', autocomplete: 'off', maxlength: isTime ? 5 : null,
            min: it.min, max: it.max, step: it.step,
            value: s[it.key] === null || s[it.key] === undefined ? '' : String(s[it.key]),
            placeholder: isTime ? '09:00' : (it.auto ? autoText(it.key) : (it.nullable ? 'նշված չէ' : null)),
            dataset: isTime ? { norm: it.key, time: '1' } : { norm: it.key },
        });
        // «9:00», «9.00», «900» → «09:00» (24-часовой формат независимо от языка Windows)
        if (isTime) inp.addEventListener('change', () => { inp.value = normTime(inp.value); });
        const hint = it.hint ? h('div', { class: 'rt-field-hint', id: id + '_h', text: it.hint }) : null;
        if (hint) inp.setAttribute('aria-describedby', hint.id);
        reg(['settings.' + it.key], inp, err, it.label);
        const label = h('label', { for: id, text: it.label });
        // «авто · по GPS 1,40» не влезает в узкое поле справа от подписи — поле на всю ширину под ней;
        // рядом с подписью — откуда цифра: «авто по GPS», «по умолчанию» или «вручную»
        const src = it.auto ? h('span', { class: 'rt-badge rt-field-src', dataset: { src: it.key } }) : null;
        return h('div', { class: 'rt-field' }, it.auto ? [h('div', { class: 'rs-lblrow' }, label, src), inp] : h('div', { class: 'rt-field-row' }, label, inp), hint, err);
    }

    // Откуда цифра у «авто»-полей: пусто — по GPS (или по умолчанию, если GPS мало), заполнено — вручную
    function updateSources() {
        document.querySelectorAll('#rsForm [data-src]').forEach(el => {
            const inp = document.querySelector('[data-norm="' + el.dataset.src + '"]');
            const manual = !!inp && String(inp.value).trim() !== '';
            const gps = num((state.data.calibration || {})[el.dataset.src]) !== null;
            el.className = 'rt-badge rt-field-src ' + (manual ? 'b-manual' : (gps ? 'b-gps' : 'b-none'));
            el.textContent = manual ? 'ձեռքով' : (gps ? 'ավտոմատ՝ GPS-ով' : 'լռելյայն');
        });
    }

    // «Обязательно заполнить»: склад, машины, цены топлива — сколько из трёх заполнено (по тому, что сейчас в форме)
    function renderProgress() {
        if (!state.data) return;
        const set = (id, st, text) => {
            const el = $(id);
            el.className = 'rt-card-state is-' + st;
            el.textContent = '';
            el.append(icon(st === 'ok' ? 'fa-circle-check' : 'fa-circle-exclamation'), text);
        };
        const dp = parseCoord($('rsDepot').value);
        const depotOk = !dp.empty && !dp.error;
        set('rsStDepot', depotOk ? 'ok' : 'todo', depotOk ? 'լրացված է' : 'նշված չէ');

        // машины: в расчёте — работающие машины с тоннажем и расходом; нужна хотя бы одна
        const rows = [...$('rsManagers').querySelectorAll('tbody tr')];
        const inCalc = rows.filter(tr => tr.querySelector('[data-f="inc"]').checked);
        let full = 0, noData = 0;
        $('rsTrucks').querySelectorAll('tbody tr').forEach(tr => {
            const q = (f) => tr.querySelector('[data-f="' + f + '"]');
            if (!q('active').checked) return;
            if (String(q('cap').value).trim() !== '' && String(q('fuel').value).trim() !== '') full += 1;
            else noData += 1;
        });
        const trucksOk = full > 0 && !noData;
        set('rsStTrucks', trucksOk ? 'ok' : (full ? 'part' : 'todo'), trucksOk ? 'լրացված է · ' + full + NB + 'մեքենա'
            : (full ? 'հաշվարկում՝ ' + full + ', առանց տոննաժի կամ ծախսի՝ ' + noData : 'տոննաժով և ծախսով մեքենա չկա'));

        // цены: дизель (грузовики) и то топливо, на котором ездят менеджеры в расчёте
        const need = new Set(['diesel']);
        inCalc.forEach(tr => need.add(tr.querySelector('[data-f="type"]').value || 'petrol'));
        const names = { diesel: 'դիզել', petrol: 'բենզին', lpg: 'գազ' };
        const miss = [...need].filter(k => { const i = document.querySelector('[data-norm="fuel_price_' + k + '"]'); return !i || String(i.value).trim() === ''; });
        const fuelOk = !miss.length;
        set('rsStFuel', fuelOk ? 'ok' : 'todo', fuelOk ? 'լրացված է' : 'նշված չէ՝ ' + miss.map(k => names[k] || k).join(', '));

        // малый центр: работающие машины, которым можно в центр (авто — JAC)
        const toCenter = [...$('rsTrucks').querySelectorAll('tbody tr')].filter(tr => {
            const sel = tr.querySelector('[data-f="center"]');
            return tr.querySelector('[data-f="active"]').checked && (sel.value === 'yes' || (sel.value === 'auto' && sel.dataset.auto === '1'));
        }).length;
        set('rsStCenter', toCenter ? 'ok' : 'part', toCenter ? 'կենտրոն՝ ' + toCenter + NB + 'մեքենա'
            : 'չկա մեքենա, որը կարող է մտնել կենտրոն');

        // дома менеджеров в расчёте
        const noHome = inCalc.filter(tr => tr.querySelector('.rs-home .b-warn')).length;
        set('rsStManagers', noHome ? 'part' : 'ok', noHome ? 'տունը չի գտնվել՝ ' + noHome + NB + 'մենեջեր' : 'տները նշված են');

        const n = [depotOk, trucksOk, fuelOk].filter(Boolean).length;
        $('rsProgress').querySelectorAll('.rt-progress-bar > span').forEach((el, i) => el.classList.toggle('is-ok', i < n));
        const t = $('rsProgressText');
        t.textContent = n === 3 ? 'Ամեն ինչ լրացված է' : 'Լրացված է՝ ' + n + ' / 3';
        t.classList.toggle('is-done', n === 3);
        $('rsStep1').classList.toggle('is-done', n === 3);
    }

    // Подсказка в пустом поле нормы дорог: что возьмёт расчёт — калибровку по GPS или значение по умолчанию
    function autoText(key) {
        const r = CALIB.find(x => x.key === key);
        const gps = num((state.data.calibration || {})[key]);
        return gps !== null
            ? 'ավտոմատ · GPS-ով ' + gps.toLocaleString('ru-RU', { minimumFractionDigits: r.d, maximumFractionDigits: r.d })
            : 'ավտոմատ · լռելյայն ' + fmt(r.def, r.d);
    }

    function chainsGroup(s) {
        const chosen = new Set((Array.isArray(s.chain_groups) ? s.chain_groups : []).map(String));
        const groups = state.data.customer_groups;
        const err = errNode();
        const list = h('div', { class: 'rt-groups', id: 'rsChains', role: 'group', 'aria-label': 'Հաճախորդների խմբեր՝ ցանցեր' });
        groups.forEach((g, i) => {
            const cid = 'rsCg' + i;
            list.append(h('label', { class: 'rt-group-item', for: cid, dataset: { q: ((g.name || '') + ' ' + (g.code || '')).toLowerCase() } },
                h('input', { type: 'checkbox', id: cid, value: String(g.code), checked: chosen.has(String(g.code)), dataset: { chain: '1' } }),
                h('span', { class: 'n', text: g.name || g.code || '—', title: g.name || '' }),
                num(g.customers) !== null ? h('span', { class: 'cnt', text: fmt(g.customers) }) : null));
        });
        const search = h('input', { class: 'rt-input w-100 mb-2', type: 'search', placeholder: 'Գտնել խումբ…',
            'aria-label': 'Հաճախորդների խմբի որոնում', dataset: { noTrack: '1' } });
        search.addEventListener('input', () => {
            const q = search.value.trim().toLowerCase();
            list.querySelectorAll('.rt-group-item').forEach(el => { el.hidden = !!q && !el.dataset.q.includes(q); });
        });
        reg(['settings.chain_groups'], list, err, 'Ցանցեր');
        const group = h('div', { class: 'rt-group', style: 'grid-column: 1 / -1' },
            h('h3', { text: 'Ցանցերը միշտ «խոշոր» խանութ են' }),
            h('p', { class: 'rt-field-hint mb-2', text: 'Նշեք ERP-ի հաճախորդների այն խմբերը, որոնք համարվում են ցանցեր՝ դրանց այցը տևում է այնքան, որքան խոշոր խանութինը։' }),
            groups.length ? search : null,
            groups.length ? list : h('p', { class: 'rt-field-hint', text: 'ERP-ում հաճախորդների խմբեր չկան։' }),
            err);
        return group;
    }

    // ---------- 05 · Сезон ----------
    function renderSeason() {
        const box = $('rsSeason');
        box.textContent = '';
        const s = state.data.settings, se = state.data.season;
        // Если вручную задан только один сезон (другой — авто), второй начинаем с действующего в расчёте
        // (найденного автоматом или 3 крайних), иначе при сохранении он молча превратился бы в пустой список.
        const manual = Array.isArray(s.low_months) || Array.isArray(s.peak_months);
        state.season.mode = manual ? 'manual' : 'auto';
        const pick = (own, eff, detected) => (Array.isArray(own) ? own
            : (Array.isArray(eff) ? eff : (Array.isArray(detected) ? detected : []))).map(Number);
        state.season.low = new Set(pick(manual ? s.low_months : null, se.low_months, se.detected_low));
        state.season.peak = new Set(pick(manual ? s.peak_months : null, se.peak_months, se.detected_peak));

        const radio = (val, text) => h('label', {}, h('input', { type: 'radio', name: 'rsSeasonMode', value: val, checked: state.season.mode === val }), text);
        const seg = h('div', { class: 'rt-seg', role: 'radiogroup', 'aria-label': 'Ինչպես ընտրել սեզոնների ամիսները' },
            radio('auto', 'Ավտոմատ՝ ըստ վաճառքի'), radio('manual', 'Ընտրել ձեռքով'));
        seg.addEventListener('change', (e) => {
            if (e.target.name !== 'rsSeasonMode') return;
            if (e.target.value === 'manual') {
                const eff = effectiveSeason();   // вручную — начинаем с того, что нашёл автомат
                state.season.low = new Set(eff.low);
                state.season.peak = new Set(eff.peak);
            }
            state.season.mode = e.target.value;
            updateSeasonUI();
        });

        const thr = h('div', { class: 'rt-form-grid mt-3', id: 'rsSeasonThr' });
        [
            { key: 'low_season_index_max', label: 'Ձմեռ՝ ամիսներ, որոնց վաճառքը միջինի … բաժնից բարձր չէ', min: 0.1, max: 1, step: 0.05 },
            { key: 'peak_season_index_min', label: 'Ամառ՝ ամիսներ, որոնց վաճառքը միջինի … բաժնից ցածր չէ', min: 1, max: 3, step: 0.05 },
        ].forEach(it => thr.append(normField(it, s)));

        const bars = h('div', { class: 'rt-months', id: 'rsMonths', 'aria-hidden': 'true' });
        const row = (kind, title, ico) => {
            const wrap = h('div', { class: 'rt-monthrow', role: 'group', 'aria-label': title, id: 'rsRow_' + kind },
                h('span', { class: 'lbl' }, icon(ico), title));
            MONTHS.forEach((m, i) => {
                const b = h('button', { type: 'button', class: 'rt-mchip m-' + kind, title: MONTHS_FULL[i], dataset: { m: String(i + 1), kind } }, m,
                    h('span', { class: 'rt-sr-only', text: ' (' + MONTHS_FULL[i] + ')' }));
                b.addEventListener('click', () => toggleMonth(kind, i + 1));
                wrap.append(b);
            });
            return wrap;
        };
        const err = errNode();
        const rows = h('div', {}, row('low', 'Ցածր սեզոն (ձմեռ)', 'fa-snowflake'), row('peak', 'Բարձր սեզոն (ամառ)', 'fa-sun'));
        reg(['settings.low_months', 'settings.peak_months'], rows, err, 'Սեզոնների ամիսներ');
        box.append(
            h('p', { class: 'rt-lead', text: 'Օրվա հասույթը (100 000) ստուգում ենք ցածր սեզոնով, մեքենաների բեռնվածությունը՝ բարձր սեզոնով։ '
                + 'Ծրագիրը յուրաքանչյուր ամիս համեմատում է վաճառքի երեք տարվա միջինի հետ։' }),
            seg, thr, bars, rows, err,
            h('p', { class: 'rt-field-hint mt-2', id: 'rsSeasonSummary', 'aria-live': 'polite' }),
            h('p', { class: 'rt-season-fallback', id: 'rsSeasonFallback', hidden: true }));
        updateSeasonUI();
    }

    function seasonIndex() {
        const idx = state.data.season.index;
        const out = [];
        for (let m = 1; m <= 12; m++) out.push(idx && typeof idx === 'object' ? num(idx[m] ?? idx[String(m)]) : null);
        return out;
    }

    // Месяцы сезонов, которые возьмёт расчёт: {low, peak, fallback: ['low'|'peak']} — fallback, если
    // порогам не подошёл ни один месяц и взяты 3 крайних по индексу (как resolve_season на сервере)
    function effectiveSeason() {
        if (state.season.mode === 'manual') return { low: [...state.season.low], peak: [...state.season.peak], fallback: [] };
        const s = state.data.settings, se = state.data.season;
        const lowInp = document.querySelector('[data-norm="low_season_index_max"]');
        const peakInp = document.querySelector('[data-norm="peak_season_index_min"]');
        const lowMax = lowInp ? num(lowInp.value) : null, peakMin = peakInp ? num(peakInp.value) : null;
        const idx = seasonIndex();
        // сохранено «авто» с теми же порогами — берём действующие месяцы из ответа сервера
        const savedAuto = !Array.isArray(s.low_months) && !Array.isArray(s.peak_months);
        const same = lowMax === num(s.low_season_index_max) && peakMin === num(s.peak_season_index_min);
        if (savedAuto && same && Array.isArray(se.low_months) && Array.isArray(se.peak_months)) {
            return { low: se.low_months.map(Number), peak: se.peak_months.map(Number),
                     fallback: [se.fallback_low ? 'low' : null, se.fallback_peak ? 'peak' : null].filter(Boolean) };
        }
        if (!idx.some(v => v !== null)) {   // индекса нет — пересчитывать нечего
            return { low: (se.detected_low || []).map(Number), peak: (se.detected_peak || []).map(Number), fallback: [] };
        }
        const low = [], peak = [], fallback = [];
        idx.forEach((v, i) => {
            if (v === null) return;
            if (lowMax !== null && v <= lowMax) low.push(i + 1);
            else if (peakMin !== null && v >= peakMin) peak.push(i + 1);
        });
        if (!low.length) { low.push(...extremeMonths(idx, peak, false)); fallback.push('low'); }
        if (!peak.length) { peak.push(...extremeMonths(idx, low, true)); fallback.push('peak'); }
        return { low, peak, fallback };
    }

    // 3 месяца с самым низким (highest — высоким) индексом, кроме exclude; ничья — по номеру месяца
    function extremeMonths(idx, exclude, highest) {
        return idx.map((v, i) => [v, i + 1])
            .filter(([v, m]) => v !== null && !exclude.includes(m))
            .sort((a, b) => (highest ? b[0] - a[0] : a[0] - b[0]) || a[1] - b[1])
            .slice(0, 3).map(x => x[1]).sort((a, b) => a - b);
    }

    function toggleMonth(kind, m) {
        if (state.season.mode !== 'manual') return;
        const own = state.season[kind], other = state.season[kind === 'low' ? 'peak' : 'low'];
        if (own.has(m)) own.delete(m);
        else { own.add(m); other.delete(m); }   // месяц не может быть и зимой, и летом
        updateSeasonUI();
        updateDirty();
    }

    function updateSeasonUI() {
        const eff = effectiveSeason(), manual = state.season.mode === 'manual';
        const low = new Set(eff.low), peak = new Set(eff.peak);
        document.querySelectorAll('#rsSeason .rt-mchip').forEach(b => {
            const m = +b.dataset.m, on = b.dataset.kind === 'low' ? low.has(m) : peak.has(m);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
            b.disabled = !manual;
        });
        $('rsSeasonThr').querySelectorAll('input').forEach(i => { i.disabled = manual; });
        const idx = seasonIndex(), bars = $('rsMonths');
        if (!idx.some(v => v !== null)) {
            bars.innerHTML = '';
            bars.hidden = true;
        } else {
            bars.hidden = false;
            const top = Math.max(1.5, ...idx.filter(v => v !== null)) * 1.05;
            bars.innerHTML = idx.map((v, i) => {
                const m = i + 1, cls = low.has(m) ? ' is-low' : (peak.has(m) ? ' is-peak' : '');
                return '<div class="rt-month' + cls + '"><div class="rt-month-track" style="--one:' + (100 / top).toFixed(1) + '%">'
                    + '<div class="rt-month-bar" style="height:' + (v === null ? 0 : Math.max(2, v / top * 100)).toFixed(1) + '%"></div></div>'
                    + '<span class="m">' + MONTHS[i] + '</span><span class="i">' + (v === null ? '—' : fmt(v, 2)) + '</span></div>';
            }).join('');
        }
        const list = (set) => set.size ? [...set].sort((a, b) => a - b).map(m => MONTHS[m - 1]).join(', ') : 'չկա';
        $('rsSeasonSummary').textContent = (manual ? 'Ընտրված է ձեռքով։ ' : (idx.some(v => v !== null)
            ? 'Գտնված է ըստ վաճառքի։ ' : 'Ավտոմատ ընտրության համար վաճառքը քիչ է — ընտրեք ամիսները ձեռքով։ '))
            + 'Ձմեռ՝ ' + list(low) + '։ Ամառ՝ ' + list(peak) + '։';
        const fbLow = eff.fallback.includes('low'), fbPeak = eff.fallback.includes('peak');
        const note = $('rsSeasonFallback');
        note.textContent = (fbLow && fbPeak)
            ? 'Ոչ մի ամիս չհամապատասխանեց շեմերին — վերցված են ամենացածր և ամենաբարձր վաճառքով 3-ական ամիսները։ Շեմերը կարելի է ուղղել վերևում։'
            : (fbLow ? 'Ոչ մի ամիս չհամապատասխանեց ձմռան շեմին — վերցված են ամենացածր վաճառքով 3 ամիսները։ Շեմը կարելի է ուղղել վերևում։'
                : (fbPeak ? 'Ոչ մի ամիս չհամապատասխանեց ամռան շեմին — վերցված են ամենաբարձր վաճառքով 3 ամիսները։ Շեմը կարելի է ուղղել վերևում։' : ''));
        note.hidden = !(fbLow || fbPeak);
    }

    // ---------- 06 · Калибровка ----------
    function renderCalib() {
        const box = $('rsCalib');
        box.textContent = '';
        const c = state.data.calibration;
        const avail = !!c && CALIB.some(r => num(c[r.key]) !== null);
        if (!avail) {
            box.append(h('p', { class: 'rt-empty px-0 mb-0', text: 'Հուշումների համար GPS տվյալները դեռ քիչ են՝ անհրաժեշտ են օրեր, երբ մենեջերն ունի առնվազն 8 այց և մանրամասն հետագիծ։ '
                + '«Նորմեր և կանոններ» բաժնի դատարկ դաշտերը հաշվվում են լռելյայն՝ ոլորունություն 1,3, արագություն՝ 25 կմ/ժ քաղաքում և 45 կմ/ժ մարզում, '
                + 'այց՝ 7 / 10 / 20 րոպե (փոքր / միջին / խոշոր խանութ)։' }));
            return;
        }
        const rows = h('div', { class: 'rt-calib' });
        const appliers = [];
        CALIB.forEach(r => {
            const sug = num(c[r.key]);
            if (sug === null) return;
            const val = round(sug, r.d);
            const inp = document.querySelector('[data-norm="' + r.key + '"]');
            const cur = h('span', { class: 'cur', dataset: { calibCur: r.key } });
            const btn = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Կիրառել՝ ' + r.label + ' ' + fmt(val, r.d) },
                icon('fa-check'), 'Կիրառել');
            const apply = () => {
                if (!inp) return;
                inp.value = String(val);
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                flash(inp);
            };
            btn.addEventListener('click', () => { apply(); announce(r.label + '՝ տեղադրվեց ' + fmt(val, r.d)); });
            appliers.push(apply);
            rows.append(h('div', { class: 'rt-calib-row', dataset: { calibRow: r.key } },
                h('span', { class: 'k', text: r.label }),
                cur,
                h('span', { class: 'sug' }, h('small', { text: 'GPS-ով' }), fmt(val, r.d) + r.unit),
                btn));
        });
        const all = h('button', { type: 'button', class: 'rt-btn rt-btn-ghost mt-2' }, icon('fa-check-double'), 'Կիրառել բոլորը');
        all.addEventListener('click', () => { appliers.forEach(f => f()); announce('GPS-ի հուշումները տեղադրվեցին «Նորմեր և կանոններ» բաժնում'); });
        const avgVisit = num(c.visit_min_avg);
        box.append(
            h('p', { class: 'rt-lead', text: 'Հաշվված է մենեջերների GPS հետագծերով՝ վերջին 6 շաբաթում'
                + (num(c.days_used) !== null ? ' (' + fmt(c.days_used) + NB + 'օր)' : '')
                + '։ Արագությունը հաշվվում է միայն այն ժամանակ, երբ մեքենան շարժվում է՝ խանութների միջև քայլելն այն չի նվազեցնում։ '
                + (avgVisit !== null ? 'Հաճախորդի մոտ մենեջերը միջինում մնում է ' + fmt(avgVisit, 1) + NB + 'րոպե մեկ այցի ընթացքում։ Փոքր խանութի այցը '
                    + 'միջին խանութի այցի 0,7-ն է, խոշորինը՝ երկու անգամ ավելի երկար․ միջինն ընտրված է այնպես, որ պլանով այցը միջինում տևի նույնքան։ ' : '')
                + '«Նորմեր և կանոններ» բաժնի դատարկ դաշտը նշանակում է ավտոմատ՝ հաշվարկն ինքն է վերցնում այս թվերը GPS-ից և թարմացնում դրանք հետագծերի հետ միասին։ '
                + '«Կիրառել» կոճակը թիվն ամրացնում է դաշտում — պահպանեք, որ այն գործի։' }),
            rows, all);
        updateCalib();
    }

    // «Сейчас» в калибровке всегда показывает то, что стоит в поле норм; пустое поле — авто, т.е. та же цифра GPS
    function updateCalib() {
        const c = state.data && state.data.calibration;
        if (!c) return;
        document.querySelectorAll('[data-calib-cur]').forEach(el => {
            const r = CALIB.find(x => x.key === el.dataset.calibCur);
            const inp = document.querySelector('[data-norm="' + r.key + '"]');
            const v = inp ? num(inp.value) : null;
            const auto = !!inp && String(inp.value).trim() === '' && !(inp.validity && inp.validity.badInput);
            el.textContent = '';
            el.append(h('small', { text: 'հիմա' }), auto ? 'ավտոմատ (GPS-ով)' : (v === null ? '—' : fmt(v, r.d) + r.unit));
            const same = auto || (v !== null && round(v, r.d) === round(num(c[r.key]), r.d));
            el.closest('.rt-calib-row').classList.toggle('is-same', same);
        });
    }

    // ---------- Сбор и сохранение ----------
    function collect() {
        const errors = {};
        const s = Object.assign({}, state.data.settings);   // неизвестные ключи отдаём как были
        if ($('rsTrafficMode')) s.traffic_mode = $('rsTrafficMode').value;
        document.querySelectorAll('#rsForm [data-norm]').forEach(inp => {
            const key = inp.dataset.norm;
            if (inp.dataset.time) {
                const v = normTime(inp.value);
                if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(v)) errors['settings.' + key] = 'Գրեք ժամը ԺԺ:ՐՐ ձևաչափով, օրինակ՝ 09:00';
                else s[key] = v;
                return;
            }
            const it = NORMS.flatMap(g => g.items).find(x => x.key === key);
            const r = readNum(inp, !!(it && it.nullable));
            if (r.error) errors['settings.' + key] = r.error;
            else s[key] = r.value;
        });
        s.workdays = [...document.querySelectorAll('#rsForm [data-wd]:checked')].map(i => Number(i.value));
        if (!s.workdays.length) errors['settings.workdays'] = 'Նշեք գոնե մեկ աշխատանքային օր';
        const cc = parseCoord($('rsN_city_center').value);
        if (cc.empty) errors['settings.city_center_lat'] = 'Նշեք քաղաքի կենտրոնը';
        else if (cc.error) errors['settings.city_center_lat'] = cc.error;
        else { s.city_center_lat = cc.lat; s.city_center_lon = cc.lon; }
        s.chain_groups = [...document.querySelectorAll('#rsForm [data-chain]:checked')].map(i => i.value);
        s.center_zone = state.zone.map(([a, b]) => [round(a, 6), round(b, 6)]);
        const manual = state.season.mode === 'manual';
        s.low_months = manual ? [...state.season.low].sort((a, b) => a - b) : null;
        s.peak_months = manual ? [...state.season.peak].sort((a, b) => a - b) : null;

        let depot = null;
        const dp = parseCoord($('rsDepot').value);
        if (dp.error) errors.depot = dp.error;
        else if (!dp.empty) depot = { lat: dp.lat, lon: dp.lon };

        const trucks = [], manualTrucks = [];
        // ошибки сервера по ручным машинам приходят по номеру в списке — тот же номер у полей строки
        const alias = (from, to) => { const f = findField(from); if (f) state.fields.set(normKey(to), f); };
        $('rsTrucks').querySelectorAll('tbody tr').forEach(tr => {
            const q = (f) => tr.querySelector('[data-f="' + f + '"]');
            const cap = readNum(q('cap'), true), fuel = readNum(q('fuel'), true);
            const m = tr.dataset.mk ? state.manual.find(x => x.key === tr.dataset.mk) : null;
            const p = m ? 'manual_trucks.' + manualTrucks.length + '.' : 'trucks.' + tr.dataset.i + '.';
            if (cap.error) errors[p + 'capacity_kg'] = cap.error;
            else if (cap.value !== null && (cap.value < 0.1 || cap.value > 30)) errors[p + 'capacity_kg'] = 'Տոննաժը՝ 0,1-ից մինչև 30 տ';
            if (fuel.error) errors[p + 'fuel_l_per_100km'] = fuel.error;
            const item = {
                car_code: m ? m.car_code : state.data.trucks[+tr.dataset.i].car_code,
                capacity_kg: cap.value === null || cap.value === undefined ? null : Math.round(cap.value * 1000),
                fuel_l_per_100km: fuel.value === undefined ? null : fuel.value,
            };
            LOAD_COSTS.forEach(([f, , min, max]) => {
                const value = readNum(q(f), true);
                if (value.error) errors[p + f] = value.error;
                else if (value.value !== null && (value.value < min || value.value > max)) errors[p + f] = 'Արժեքը՝ ' + min + '-ից մինչև ' + max;
                item[f] = value.value === undefined ? null : value.value;
            });
            const empty = item.fuel_empty_l_per_100km, full = item.fuel_full_l_per_100km;
            if ((empty === null) !== (full === null)) {
                errors[p + 'fuel_empty_l_per_100km'] = errors[p + 'fuel_full_l_per_100km'] = 'Նշեք դատարկ և լրիվ բեռնված մեքենայի ծախսը միասին';
            } else if (empty !== null && full < empty) errors[p + 'fuel_full_l_per_100km'] = 'Լրիվ բեռնված մեքենայի ծախսը չի կարող պակաս լինել դատարկի ծախսից';
            const act = q('active');
            // «В центр»: авто — null (решает название машины), иначе выбор владельца
            const cm = q('center').value;
            item.center_ok = cm === 'auto' ? null : cm === 'yes';
            if (m) {
                ['capacity_kg', 'fuel_l_per_100km', 'active', 'car_code', 'name', 'van_agent_id', 'center_ok']
                    .concat(LOAD_COSTS.map(([f]) => f))
                    .forEach(f => alias('manual_trucks.' + m.car_code + '.' + f, p + f));
                alias('manual_trucks.' + m.car_code, p.slice(0, -1));
                manualTrucks.push(Object.assign(item, { name: m.name || null, active: act.checked,
                    van_agent_id: m.van_agent_id === undefined ? null : m.van_agent_id }));
                return;
            }
            // «Работает»: ручной выбор — true/false; нажали «вернуть авто» — null; «авто» без изменений не шлём —
            // иначе сохранение заморозило бы решение по накладным
            if (act.dataset.mode === 'manual') item.active = act.checked;
            else if (state.data.trucks[+tr.dataset.i].active_source === 'manual') item.active = null;
            trucks.push(item);
        });

        const managers = [];
        $('rsManagers').querySelectorAll('tbody tr').forEach(tr => {
            const i = +tr.dataset.i, m = state.data.managers[i];
            const q = (f) => tr.querySelector('[data-f="' + f + '"]');
            const home = parseCoord(q('home').value);
            if (home.error) errors['managers.' + i + '.home_lat'] = home.error;
            const fuel = readNum(q('fuel'), true);
            if (fuel.error) errors['managers.' + i + '.car_fuel_l_per_100km'] = fuel.error;
            const item = {
                agent_id: m.agent_id,
                home_lat: home.empty || home.error ? null : home.lat,
                home_lon: home.empty || home.error ? null : home.lon,
                car_fuel_l_per_100km: fuel.value === undefined ? null : fuel.value,
                car_fuel_type: q('type').value || null,
            };
            // «В расчёте»: ручной выбор — true/false; нажали «вернуть авто» — null; «авто» без
            // изменений не шлём — иначе сохранение заморозило бы решение по работе за 8 недель
            const inc = q('inc');
            if (inc.dataset.mode === 'manual') item.included = inc.checked;
            else if (m.included_source === 'manual') item.included = null;
            managers.push(item);
        });
        return { payload: { settings: s, depot, trucks, managers, manual_trucks: manualTrucks }, errors };
    }

    function snapshot() {
        const vals = [...document.querySelectorAll('#rsForm input, #rsForm select')]
            .filter(el => !el.dataset.noTrack)
            .map(el => (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? '1' : '0') : el.value);
        // «вернуть авто» может не менять галочку — режим «В расчёте» тоже изменение
        const incModes = [...document.querySelectorAll('#rsManagers [data-f="inc"], #rsTrucks [data-f="active"]')].map(el => el.dataset.mode || '');
        // ручные машины: номер, название и экспедитор живут не в полях строки
        const manual = (state.manual || []).map(m => [m.car_code, m.name || null, m.van_agent_id === undefined ? null : m.van_agent_id]);
        return JSON.stringify([vals, incModes, manual, state.season.mode, [...state.season.low].sort(), [...state.season.peak].sort(),
            state.zone.map(([a, b]) => [round(a, 6), round(b, 6)])]);
    }

    function updateDirty() {
        if (!state.data) return;
        state.dirty = snapshot() !== state.initial;
        const el = $('rsDirty');
        el.textContent = state.dirty ? 'Կան չպահպանված փոփոխություններ' : 'Փոփոխություններ չկան';
        el.classList.toggle('is-dirty', state.dirty);
    }

    function setSaving(on) {
        state.saving = on;
        const btn = $('rsSaveBtn');
        btn.disabled = on;
        btn.setAttribute('aria-busy', on ? 'true' : 'false');
        btn.querySelector('i').className = on ? 'rt-spin-inline' : 'fas fa-floppy-disk';
        btn.querySelector('span').textContent = on ? 'Պահպանում եմ…' : 'Պահպանել';
    }

    async function save() {
        if (state.saving || !state.data) return;
        clearErrors();
        const { payload, errors } = collect();
        if (Object.keys(errors).length) { showErrors(errors, true); return; }
        setSaving(true);
        try {
            let resp;
            try {
                resp = await fetch('/api/routes/settings', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify(payload),
                });
            } catch (e) {
                showSaveError('Սերվերի հետ կապ չկա — կարգավորումները չեն պահպանվել։ Ստուգեք ցանցը և կրկին սեղմեք «Պահպանել»։');
                return;
            }
            let data = null;
            try { data = await resp.json(); } catch (e) { data = null; }
            if (resp.ok && data && data.success === true) {
                state.initial = snapshot();
                updateDirty();
                const toast = $('rsToast');
                if (window.bootstrap && bootstrap.Toast) bootstrap.Toast.getOrCreateInstance(toast).show();
                announce('Կարգավորումները պահպանված են');
                await load(true);   // сервер мог привести значения к своему виду
                return;
            }
            if (data && data.errors && typeof data.errors === 'object' && Object.keys(data.errors).length) {
                showErrors(data.errors, false);
                return;
            }
            showSaveError(authText(resp, data) || (data && data.error) || ('Չհաջողվեց պահպանել (կոդ ' + resp.status + ')։ Փորձեք կրկին։'));
        } finally {
            setSaving(false);
        }
    }

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        syncNavOffset();
        window.addEventListener('resize', syncNavOffset);
        const form = $('rsForm');
        form.addEventListener('submit', (e) => { e.preventDefault(); save(); });
        form.addEventListener('input', (e) => {
            const t = e.target;
            if (t.id === 'rsDepot') syncDepot({ pan: false, strict: false });
            if (t.dataset && (t.dataset.norm === 'low_season_index_max' || t.dataset.norm === 'peak_season_index_min')) updateSeasonUI();
            if (t.dataset && t.dataset.norm) { updateCalib(); updateSources(); }
            updateDirty();
            renderProgress();
        });
        form.addEventListener('change', () => { updateDirty(); renderProgress(); });
        // колесо мыши не должно менять число в поле, над которым случайно прокручивают страницу
        form.addEventListener('wheel', (e) => {
            if (e.target.type === 'number' && e.target === document.activeElement) e.target.blur();
        }, { passive: true });
        const depot = $('rsDepot');
        depot.addEventListener('change', () => syncDepot({ pan: true, strict: true }));
        depot.addEventListener('paste', () => setTimeout(() => syncDepot({ pan: true, strict: true }), 0));
        $('rsDepotClear').addEventListener('click', () => {
            depot.value = '';
            syncDepot({ pan: false, strict: true });
            updateDirty();
            depot.focus();
        });
        $('rsCenterReset').addEventListener('click', () => {
            const z = state.data && state.data.center_zone_default;
            if (!Array.isArray(z)) return;
            state.zone = z.map(p => [Number(p[0]), Number(p[1])]);
            drawZone(true);
            zoneChanged('Կենտրոնի սահմանը վերադարձվեց սկզբնական վիճակին');
        });
        $('rsRetryBtn').addEventListener('click', () => load(false));
        window.addEventListener('beforeunload', (e) => {
            if (state.dirty) { e.preventDefault(); e.returnValue = ''; }
        });
        load(false);
    });
})();

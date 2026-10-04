/* Условия магазина в /routes/settings; время доставки, время у магазина (№50) и машины сохраняются одной транзакцией.
   Тексты — по-армянски (решение владельца №58), подсказка о времени у магазина — как на «Развозе» (routes_dispatch.js). */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    let shop = null, vehicles = [], norms = null, busy = false, searchTimer = null, searchGen = 0;
    const minutes = v => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
    const hhmm = m => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
    const nameOf = item => item.name || item.code || String(item.customer_id);
    const truckLabel = t => [t.name, t.car_code].filter(Boolean).join(' · ');
    // Ответы дашборда (вход, доступ) приходят по-русски — свой армянский текст по коду ответа
    const AUTH_HY = { 401: 'Անհրաժեշտ է մուտք գործել համակարգ։', 403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։' };

    async function api(method, path, body) {
        let response;
        try {
            response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
                ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        } catch (e) { throw new Error('Սերվերի հետ կապ չկա։ Փորձեք կրկին։'); }
        let data = null;
        try { data = await response.json(); } catch (e) { data = null; }
        // 403 CSRF дашборда («сессия формы устарела») — не запрет доступа (как routes_learning.js и routes_garage.js)
        if (response.status === 403 && data && data.error === 'csrf') throw new Error('Էջը հնացել է՝ թարմացրեք այն և կրկնեք։');
        if (AUTH_HY[response.status]) throw new Error(AUTH_HY[response.status]);
        if (data === null) throw new Error('Չհաջողվեց կարդալ սերվերի պատասխանը։ Թարմացրեք էջը։');
        if (!response.ok || !data || data.success !== true) {
            const details = data && data.errors ? Object.values(data.errors).join('; ') : '';
            throw new Error(details || (data && data.error) || 'Չհաջողվեց կատարել հարցումը։');
        }
        return data;
    }
    function windowText(w) {
        if (!w) return 'Առանց ժամի սահմանափակման';
        if (w.kind === 'before') return 'Մինչև ' + hhmm(w.t1);
        if (w.kind === 'after') return hhmm(w.t1) + '-ից հետո';
        if (w.kind === 'between') return hhmm(w.t1) + '–' + hhmm(w.t2);
        return hhmm(w.t1) + (w.tol ? ' ±' + w.tol + '\u00a0րոպե' : '');
    }
    // по-армянски после числа — единственное число
    const unloads = n => n + '\u00a0բեռնաթափում';
    // Подсказка — как посчитает «Развоз»: пустое поле — обычное время или своё время магазина по факту
    // (unload_auto_min); со 2-й разгрузки по GPS (unload_visits) — время по GPS. «По факту» — только если
    // время отличается от нормы на точку больше, чем на округление: сервер шлёт его до десятых, норма обучения — до
    // сотых, разница — целые сотые, до 5 сотых — округление (8,37 → 8,4; 8,75 → 8,8), своё время по факту — от 0,5 мин.
    function unloadHint(item) {
        const perTonne = norms ? minutes(norms.per_tonne_min) + '\u00a0րոպե տոննայի համար' : 'րոպեներ տոննայի համար';
        const auto = item && item.unload_auto_min != null ? Number(item.unload_auto_min) : null;
        const byFact = auto !== null && norms && Math.round(Math.abs(auto - Number(norms.per_stop_min)) * 100) > 5;
        const empty = byFact ? minutes(auto) + '\u00a0րոպե (ըստ փաստի)'
            : norms ? 'սովորական ' + minutes(norms.per_stop_min) + '\u00a0րոպե' : 'սովորական ժամանակ';
        const fact = (item && item.unload_visits ? ' Ըստ վարորդների GPS-ի՝ այս խանութում արդեն եղել է '
            + unloads(item.unload_visits) + '։' : '')
            + ' Քանի դեռ այս խանութում GPS-ով 2 բեռնաթափում չկա, օգտագործվում է ձեր գրած ժամանակը․ 2-րդ բեռնաթափումից սկսած՝'
            + ' ծրագիրը ժամանակը վերցնում է GPS-ից։ Եթե առաջին երկու բեռնաթափումները տևողությամբ շատ են տարբերվում,'
            + ' ծրագիրը սպասում է երրորդին։';
        return 'Քանի րոպե է մեքենան կանգնում այս խանութի մոտ՝ կայանում, ընդունում, փաստաթղթեր։ Բեռի ժամանակը ('
            + perTonne + ') ծրագիրը կավելացնի ինքը։' + fact + ' Դատարկ՝ ' + empty + '։';
    }
    function vehicleText(rule) {
        if (!rule || (rule.mode === 'deny' && !rule.trucks.length)) return 'Բոլոր մեքենաները';
        if (rule.mode === 'allow' && !rule.trucks.length) return 'Ոչ մի մեքենա թույլատրված չէ';
        return (rule.mode === 'allow' ? 'Միայն՝ ' : 'Չեն կարող՝ ') + rule.trucks.join(', ');
    }
    async function search(customerId) {
        const gen = ++searchGen, query = $('rcsSearch').value.trim();
        const list = $('rcsMatches'), status = $('rcsSearchStatus');
        if (!customerId && query && query.length < 2) {
            list.textContent = ''; status.textContent = 'Գրեք առնվազն 2 նիշ։'; return;
        }
        status.textContent = 'Փնտրում եմ խանութներ…';
        try {
            const data = await api('GET', '/api/routes/customer-vehicles?q=' + encodeURIComponent(query)
                + (customerId ? '&customer_id=' + customerId : ''));
            if (gen !== searchGen) return;
            vehicles = data.vehicles || [];
            norms = data.unload_norms || null;
            list.textContent = '';
            data.customers.forEach(item => {
                const li = document.createElement('li'), button = document.createElement('button');
                button.type = 'button'; button.className = 'rcs-shop';
                const title = document.createElement('b'), note = document.createElement('span');
                title.textContent = nameOf(item) + ' · ' + (item.code || item.customer_id);
                note.textContent = windowText(item.window) + ' · ' + vehicleText(item.vehicle_access)
                    + (item.unload_min ? ' · Ժամանակ խանութում՝ ' + minutes(item.unload_min) + '\u00a0րոպե' : '');
                button.append(title, note); button.addEventListener('click', () => open(item));
                li.append(button); list.append(li);
            });
            status.textContent = data.total > data.customers.length ? 'Ցույց են տրված առաջին 30 խանութները։ Ճշտեք որոնումը։'
                : data.customers.length ? '' : query || customerId ? 'Խանութը չի գտնվել։'
                : 'Պայմաններ դեռ չկան։ Գտեք խանութը անվանումով կամ կոդով։';
            if (customerId && data.customers.length) open(data.customers[0]);
        } catch (e) { if (gen === searchGen) status.textContent = e.message; }
    }
    function open(item) {
        if (busy) return;
        shop = item;
        $('rcsShop').textContent = nameOf(item) + ' · ' + (item.code || item.customer_id);
        $('rcsError').textContent = '';
        const w = item.window;
        $('rcsTimeKind').value = w ? w.kind : '';
        $('rcsTimeT1').value = w ? hhmm(w.t1) : '';
        $('rcsTimeT2').value = w && Number.isInteger(w.t2) ? hhmm(w.t2) : '';
        $('rcsTimeTol').value = String(w && Number.isInteger(w.tol) ? w.tol : 0);
        $('rcsUnload').value = item.unload_min ? String(item.unload_min) : '';
        $('rcsUnloadHint').textContent = unloadHint(item);
        $('rcsVehicleMode').value = item.vehicle_access ? item.vehicle_access.mode : '';
        const checked = new Set(item.vehicle_access ? item.vehicle_access.trucks : []);
        const choices = [...vehicles];
        checked.forEach(code => {
            if (!choices.some(t => t.car_code === code)) choices.push({ car_code: code, name: 'Այլևս ցուցակում չէ' });
        });
        $('rcsVehicles').textContent = '';
        choices.forEach(t => {
            const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
            label.className = 'rcs-choice'; input.type = 'checkbox'; input.value = t.car_code; input.checked = checked.has(t.car_code);
            text.textContent = truckLabel(t); label.append(input, text); $('rcsVehicles').append(label);
        });
        syncTime(); syncVehicles();
        $('rcsDialog').showModal(); $('rcsTimeKind').focus();
    }
    function syncTime() {
        const kind = $('rcsTimeKind').value;
        $('rcsTimeFirst').hidden = !kind;
        $('rcsTimeFirstLabel').textContent = { at: 'Ժամ', between: 'Սկսած', before: 'Մինչև', after: 'Հետո' }[kind] || 'Ժամ';
        $('rcsTimeLast').hidden = kind !== 'between'; $('rcsTimeTolerance').hidden = kind !== 'at';
        $('rcsTimeHint').textContent = 'Խանութում ժամանման և բեռնաթափման սկզբի ժամը։'
            + (kind === 'at' ? ' 0 րոպե շեղումը նշանակում է ճիշտ նշված ժամը։' : '');
        $('rcsError').textContent = '';
    }
    function syncVehicles() {
        const mode = $('rcsVehicleMode').value;
        $('rcsVehicles').hidden = !mode; $('rcsChoicesTitle').hidden = !mode;
        $('rcsChoicesTitle').textContent = mode === 'allow' ? 'Կարող են սպասարկել' : 'Չեն կարող սպասարկել';
        $('rcsVehicleHint').textContent = mode === 'allow'
            ? 'Ընտրեք թույլատրված մեքենաները։ Եթե ոչ մեկն ընտրված չէ կամ այդ օրը չի աշխատում, խանութը կմնա առանց մեքենայի։'
            : mode === 'deny' ? 'Ընտրված մեքենաները չեն կարող սպասարկել այս խանութը։ Մնացածը կարող են։'
            : 'Կարող են սպասարկել բոլոր մեքենաները՝ հաշվի առնելով բեռնատարողությունը և մյուս սահմանափակումները։';
        $('rcsError').textContent = '';
    }
    function readWindow() {
        const kind = $('rcsTimeKind').value;
        if (!kind) return null;
        const minutes = id => {
            const m = /^(\d{2}):(\d{2})$/.exec($(id).value || '');
            return m && Number(m[1]) < 24 && Number(m[2]) < 60 ? Number(m[1]) * 60 + Number(m[2]) : null;
        };
        const t1 = minutes('rcsTimeT1'), t2 = minutes('rcsTimeT2');
        const tol = $('rcsTimeTol').value.trim() === '' ? 0 : Number($('rcsTimeTol').value);
        if (t1 === null || (kind === 'between' && t2 === null)) throw new Error('Նշեք ժամը։');
        if (kind === 'between' && t2 <= t1) throw new Error('Միջակայքի վերջը պետք է լինի սկզբից ուշ։');
        if (kind === 'at' && (!Number.isInteger(tol) || tol < 0 || tol > 120)) throw new Error('Թույլատրելի շեղումը՝ 0-ից մինչև 120 րոպե։');
        return { kind, t1, t2: kind === 'between' ? t2 : null, tol: kind === 'at' ? tol : null };
    }
    function readUnload() {
        const input = $('rcsUnload'), error = 'Գրեք ամբողջ թիվ՝ 1-ից մինչև 120 րոպե, կամ թողեք դաշտը դատարկ։';
        // нечисло в поле type=number браузер отдаёт как '' — это не «пусто»: иначе сохранённое время стёрлось бы молча
        if (input.validity && input.validity.badInput) throw new Error(error);
        const raw = input.value.trim();
        if (raw === '') return null;
        const value = Number(raw);
        if (!Number.isInteger(value) || value < 1 || value > 120) throw new Error(error);
        return value;
    }
    async function save() {
        if (busy || !shop) return;
        let window, unloadMin;
        try { window = readWindow(); unloadMin = readUnload(); } catch (e) { $('rcsError').textContent = e.message; return; }
        const mode = $('rcsVehicleMode').value;
        const access = mode ? { mode, trucks: [...$('rcsVehicles').querySelectorAll('input:checked')].map(i => i.value) } : null;
        const customerId = shop.customer_id, name = nameOf(shop);
        busy = true;
        $('rcsDialog').querySelectorAll('input, select, button').forEach(control => { control.disabled = true; });
        $('rcsError').textContent = '';
        try {
            await api('POST', '/api/routes/customer-vehicles', { customer_id: customerId, access, window, unload_min: unloadMin });
            $('rcsDialog').close();
            $('rcsSaved').textContent = '«' + name + '»՝ պայմանները պահպանվեցին բոլոր օրերի համար։ Վերակազմեք երթերը «Առաքում» էջում։';
            await search();
        } catch (e) { $('rcsError').textContent = e.message; }
        finally {
            busy = false;
            $('rcsDialog').querySelectorAll('input, select, button').forEach(control => { control.disabled = false; });
        }
    }
    function init() {
        if (!$('rsCustomerSettings')) return;
        $('rcsTimeKind').addEventListener('change', syncTime); $('rcsVehicleMode').addEventListener('change', syncVehicles);
        ['rcsTimeT1', 'rcsTimeT2', 'rcsTimeTol', 'rcsUnload'].forEach(id => $(id).addEventListener('input', () => { $('rcsError').textContent = ''; }));
        $('rcsSave').addEventListener('click', save); $('rcsCancel').addEventListener('click', () => $('rcsDialog').close());
        $('rcsDialog').addEventListener('close', () => { shop = null; });
        $('rcsDialog').addEventListener('cancel', e => { if (busy) e.preventDefault(); });
        $('rcsSearch').addEventListener('input', () => {
            ++searchGen; clearTimeout(searchTimer); searchTimer = setTimeout(() => search(), 300);
        });
        const raw = new URLSearchParams(location.search).get('customer');
        const customerId = raw && /^[1-9]\d*$/.test(raw) && Number(raw) < 2 ** 31 ? Number(raw) : null;
        if (customerId) $('rcsSearch').value = String(customerId);
        search(customerId);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

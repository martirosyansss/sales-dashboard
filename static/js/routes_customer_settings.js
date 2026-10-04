/* Условия магазина в /routes/settings; время доставки, время у магазина (№50) и машины сохраняются одной транзакцией. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    let shop = null, vehicles = [], norms = null, busy = false, searchTimer = null, searchGen = 0;
    const minutes = v => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
    const hhmm = m => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
    const nameOf = item => item.name || item.code || String(item.customer_id);
    const truckLabel = t => [t.name, t.car_code].filter(Boolean).join(' · ');

    async function api(method, path, body) {
        let response;
        try {
            response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
                ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
        } catch (e) { throw new Error('Нет связи с сервером. Попробуйте ещё раз.'); }
        let data;
        try { data = await response.json(); } catch (e) { throw new Error('Не удалось прочитать ответ сервера. Обновите страницу.'); }
        if (!response.ok || !data || data.success !== true) {
            const details = data && data.errors ? Object.values(data.errors).join('; ') : '';
            throw new Error(details || (data && data.error) || 'Не удалось выполнить запрос.');
        }
        return data;
    }
    function windowText(w) {
        if (!w) return 'Без ограничения времени';
        if (w.kind === 'before') return 'До ' + hhmm(w.t1);
        if (w.kind === 'after') return 'После ' + hhmm(w.t1);
        if (w.kind === 'between') return hhmm(w.t1) + '–' + hhmm(w.t2);
        return 'В ' + hhmm(w.t1) + (w.tol ? ' ±' + w.tol + ' мин' : '');
    }
    const unloads = n => n + ' ' + (n % 10 === 1 && n % 100 !== 11 ? 'разгрузка'
        : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'разгрузки' : 'разгрузок');
    // Подсказка — как посчитает «Развоз»: пустое поле — обычное время или своё время магазина по факту
    // (unload_auto_min); есть разгрузки по GPS (unload_visits) — введённое смешается с фактом. «По факту» — только если
    // время отличается от нормы на точку больше, чем на округление: сервер шлёт его до десятых, норма обучения — до
    // сотых, разница — целые сотые, до 5 сотых — округление (8,37 → 8,4; 8,75 → 8,8), своё время по факту — от 0,5 мин.
    function unloadHint(item) {
        const perTonne = norms ? minutes(norms.per_tonne_min) + ' мин на тонну' : 'минуты на тонну';
        const auto = item && item.unload_auto_min != null ? Number(item.unload_auto_min) : null;
        const byFact = auto !== null && norms && Math.round(Math.abs(auto - Number(norms.per_stop_min)) * 100) > 5;
        const empty = byFact ? minutes(auto) + ' мин — по факту'
            : norms ? 'обычные ' + minutes(norms.per_stop_min) + ' мин' : 'обычное время';
        const fact = item && item.unload_visits ? ' По GPS водителей у этого магазина уже ' + unloads(item.unload_visits)
            + ': введённое время программа смешает с фактом — чем больше разгрузок, тем ближе к факту.' : '';
        return 'Сколько минут машина стоит у этого магазина: парковка, приёмка, документы. Время на сам груз ('
            + perTonne + ') программа добавит сама.' + fact + ' Пусто — ' + empty + '.';
    }
    function vehicleText(rule) {
        if (!rule || (rule.mode === 'deny' && !rule.trucks.length)) return 'Все машины';
        if (rule.mode === 'allow' && !rule.trucks.length) return 'Нет разрешённых машин';
        return (rule.mode === 'allow' ? 'Только: ' : 'Кроме: ') + rule.trucks.join(', ');
    }
    async function search(customerId) {
        const gen = ++searchGen, query = $('rcsSearch').value.trim();
        const list = $('rcsMatches'), status = $('rcsSearchStatus');
        if (!customerId && query && query.length < 2) {
            list.textContent = ''; status.textContent = 'Введите не меньше 2 символов.'; return;
        }
        status.textContent = 'Ищу магазины…';
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
                    + (item.unload_min ? ' · У магазина ' + minutes(item.unload_min) + ' мин' : '');
                button.append(title, note); button.addEventListener('click', () => open(item));
                li.append(button); list.append(li);
            });
            status.textContent = data.total > data.customers.length ? 'Показаны первые 30 магазинов. Уточните поиск.'
                : data.customers.length ? '' : query || customerId ? 'Магазин не найден.'
                : 'Пока условий нет. Найдите магазин по названию или коду.';
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
            if (!choices.some(t => t.car_code === code)) choices.push({ car_code: code, name: 'Больше нет в списке' });
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
        $('rcsTimeFirstLabel').textContent = { at: 'Время', between: 'С', before: 'До', after: 'После' }[kind] || 'Время';
        $('rcsTimeLast').hidden = kind !== 'between'; $('rcsTimeTolerance').hidden = kind !== 'at';
        $('rcsTimeHint').textContent = 'Время прибытия и начала разгрузки у магазина.'
            + (kind === 'at' ? ' Отклонение 0 минут означает точно указанное время.' : '');
        $('rcsError').textContent = '';
    }
    function syncVehicles() {
        const mode = $('rcsVehicleMode').value;
        $('rcsVehicles').hidden = !mode; $('rcsChoicesTitle').hidden = !mode;
        $('rcsChoicesTitle').textContent = mode === 'allow' ? 'Могут обслуживать' : 'Не могут обслуживать';
        $('rcsVehicleHint').textContent = mode === 'allow'
            ? 'Выберите разрешённые машины. Если ни одна не выбрана или не работает в этот день, магазин останется без машины.'
            : mode === 'deny' ? 'Выбранные машины не могут обслуживать этот магазин. Остальные могут.'
            : 'Подойдут все машины с учётом грузоподъёмности и остальных ограничений.';
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
        if (t1 === null || (kind === 'between' && t2 === null)) throw new Error('Укажите время.');
        if (kind === 'between' && t2 <= t1) throw new Error('Конец интервала должен быть позже начала.');
        if (kind === 'at' && (!Number.isInteger(tol) || tol < 0 || tol > 120)) throw new Error('Допустимое отклонение — от 0 до 120 минут.');
        return { kind, t1, t2: kind === 'between' ? t2 : null, tol: kind === 'at' ? tol : null };
    }
    function readUnload() {
        const input = $('rcsUnload'), error = 'Время у магазина — целое число минут от 1 до 120. Или оставьте поле пустым.';
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
            $('rcsSaved').textContent = name + ': условия сохранены для всех дней. Пересоберите рейсы в развозе.';
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

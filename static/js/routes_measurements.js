/* Замеры хранятся в собственной базе приложения. ERP не меняется. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const fields = [['km', 'Пробег по одометру, км'], ['minutes', 'Время от начала загрузки до возвращения, мин'],
        ['liters', 'Израсходованный дизель, л'], ['loading_minutes', 'Суммарное время загрузки, мин'],
        ['kg', 'Доставлено за день, кг'], ['trips', 'Количество рейсов'],
        ['average_load_pct', 'Средняя остаточная загрузка по километрам, %'], ['maintenance_amd', 'Расходы на обслуживание, драм']];
    const fmt = v => v === null || v === undefined ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
    let trucks = [];
    function field(id, label, input) {
        const div = document.createElement('div'); div.className = 'rt-field';
        const lab = document.createElement('label'); lab.htmlFor = id; lab.textContent = label;
        input.id = id; input.className = 'rt-input'; div.append(lab, input); return div;
    }
    async function api(method, body) {
        const r = await fetch('/api/routes/measurements', { method, credentials: 'same-origin', cache: 'no-store',
            headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
        const data = await r.json();
        if (!r.ok || !data.success) throw new Error(Object.values(data.errors || {}).join('; ') || data.error || 'Ошибка запроса');
        return data;
    }
    function show(data) {
        $('rmHistory').textContent = '';
        for (const r of (data.validation || []).slice().reverse()) {
            const tr = document.createElement('tr');
            const errors = Object.entries(r.metrics).filter(([,v]) => v.error_pct !== null)
                .map(([k,v]) => ({ km: 'км', minutes: 'время', liters: 'дизель', loading_minutes: 'загрузка' }[k]) + ': ' + fmt(v.error_pct) + '%');
            const cells = [r.day + ' / ' + r.car_code, fmt(r.metrics.km.actual), fmt(r.metrics.minutes.actual), fmt(r.metrics.liters.actual),
                errors.length ? errors.join('; ') + (r.prospective ? '' : ' (ретроспективно)') : 'Нет сохранённого прогноза'];
            for (const text of cells) { const td = document.createElement('td'); td.textContent = text; tr.append(td); }
            $('rmHistory').append(tr);
        }
        if (!data.records) $('rmStatus').textContent = 'Замеров пока нет. Пустые значения остаются неизвестными.';
        $('rmSuggestions').textContent = '';
        for (const c of data.cars || []) {
            const p = document.createElement('p');
            p.textContent = c.car_code + ': ' + c.days + ' дней. ';
            if (c.fuel) p.textContent += 'Расход по проверенным замерам: пустая ' + fmt(c.fuel.base) + ', полная ' + fmt(c.fuel.base + c.fuel.slope) + ' л/100 км. ';
            else p.textContent += 'Для двух норм расхода нужны ≥12 дат с различной измеренной загрузкой и успешная проверка последних 4 дат. ';
            if (c.loading) p.textContent += 'Загрузка: ' + fmt(c.loading.base) + ' мин на рейс + ' + fmt(c.loading.slope) + ' мин/т. ';
            if (c.maintenance_amd_per_km !== null) p.textContent += 'Обслуживание за наблюдаемый период: ' + fmt(c.maintenance_amd_per_km) + ' драм/км; влияние нагрузки отдельно не подтверждено. ';
            $('rmSuggestions').append(p);
            if (c.fuel) {
                const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                btn.textContent = 'Подставить проверенные нормы расхода ' + c.car_code;
                btn.addEventListener('click', () => {
                    const i = trucks.findIndex(t => t.car_code === c.car_code && !t.manual);
                    const row = i >= 0 ? document.querySelector('#rsTrucksBody tr[data-i="' + i + '"]') : null;
                    if (!row) { $('rmStatus').textContent = 'Введите предложенные нормы в строку этой машины и сохраните настройки.'; return; }
                    for (const [key, value] of [['fuel_empty_l_per_100km', c.fuel.base], ['fuel_full_l_per_100km', c.fuel.base + c.fuel.slope]]) {
                        const input = row.querySelector('[data-f="' + key + '"]');
                        if (input) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }
                    }
                    row.querySelector('details').open = true; row.scrollIntoView();
                    $('rmStatus').textContent = 'Нормы подставлены. Проверьте и нажмите «Сохранить» у настроек.';
                });
                $('rmSuggestions').append(btn);
            }
            if (c.loading) {
                const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                btn.textContent = 'Подставить время загрузки из замеров ' + c.car_code;
                btn.addEventListener('click', () => {
                    for (const [key, value] of [['warehouse_load_fixed_min', c.loading.base], ['warehouse_load_min_per_tonne', c.loading.slope]]) {
                        const input = document.querySelector('[data-norm="' + key + '"]');
                        if (input) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }
                    }
                    $('rsAuto').open = true; $('rsTrafficMode').scrollIntoView();
                    $('rmStatus').textContent = 'Нормы подставлены. Проверьте и нажмите «Сохранить» у настроек.';
                });
                $('rmSuggestions').append(btn);
            }
        }
    }
    document.addEventListener('routes:settings', e => {
        trucks = e.detail.trucks || [];
        const select = $('rmCar');
        if (select) {
            const previous = select.value; select.textContent = '';
            for (const t of trucks) { const opt = document.createElement('option'); opt.value = t.car_code; opt.textContent = t.car_code + ' · ' + (t.name || ''); select.append(opt); }
            if (trucks.some(t => t.car_code === previous)) select.value = previous;
        }
        const report = e.detail.calibration && e.detail.calibration.traffic;
        if (e.detail.settings.traffic_mode === 'yandex') {
            $('rsTrafficStatus').textContent = e.detail.traffic_provider && e.detail.traffic_provider.configured
                ? 'Сервис подключён. Для будущей смены используется прогноз трафика на её начало; при сбое — средняя скорость с явной пометкой в «Развозе».'
                : 'Ключ сервиса пока не подключён. Расчёт использует среднюю скорость; текущие заторы неизвестны.';
            return;
        }
        $('rsTrafficStatus').textContent = report && report.hours_supported
            ? 'Проверено часовых классов: ' + report.hours_supported + '. GPS менеджеров даёт исторический профиль скорости. Текущие заторы неизвестны.'
            : 'Часовой профиль пока не прошёл независимую проверку: используется средняя скорость. Текущие заторы неизвестны.';
    });
    document.addEventListener('DOMContentLoaded', () => {
        const box = $('rmFields'); if (!box) return;
        const date = document.createElement('input'); date.type = 'date';
        const today = new Date(); date.value = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
        date.max = date.value; date.required = true;
        const car = document.createElement('select'); car.required = true;
        box.append(field('rmDay', 'Дата', date), field('rmCar', 'Машина', car));
        for (const [key, label] of fields) { const input = document.createElement('input'); input.type = 'number'; input.min = 0; input.step = key === 'trips' ? '1' : 'any'; box.append(field('rm_' + key, label, input)); }
        $('rmForm').addEventListener('submit', async e => {
            e.preventDefault(); const button = e.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
            try {
                const body = { day: $('rmDay').value, car_code: $('rmCar').value };
                for (const [key] of fields) body[key] = $('rm_' + key).value === '' ? null : Number($('rm_' + key).value);
                show(await api('POST', body)); $('rmStatus').textContent = 'Замер сохранён.';
            } catch (err) { $('rmStatus').textContent = err.message; }
            finally { button.disabled = false; }
        });
        api('GET').then(show).catch(err => { $('rmStatus').textContent = err.message; });
    });
})();

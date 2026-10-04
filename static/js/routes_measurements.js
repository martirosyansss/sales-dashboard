/* Замеры хранятся в собственной базе приложения. ERP не меняется. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    // Тексты — на армянском (решение владельца №58, docs/research/armenian-glossary.md §3.10)
    const fields = [['km', 'Վազք ըստ օդոմետրի, կմ'], ['minutes', 'Ժամանակ՝ բեռնման սկզբից մինչև վերադարձ, րոպե'],
        ['liters', 'Ծախսված դիզել, լ'], ['loading_minutes', 'Բեռնման ընդհանուր ժամանակ, րոպե'],
        ['kg', 'Օրվա ընթացքում առաքված, կգ'], ['trips', 'Երթերի քանակ'],
        ['average_load_pct', 'Միջին մնացորդային բեռնվածություն ըստ կիլոմետրերի, %'], ['maintenance_amd', 'Տեխսպասարկման ծախսեր, դրամ']];
    // Ответ не из раздела маршрутов (вход, доступ — по-русски) не показываем: армянский текст сервера — как есть
    const HY = /[\u0531-\u058F]/;
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
        if (!r.ok || !data.success) throw new Error(Object.values(data.errors || {}).join('; ')
            || (typeof data.error === 'string' && HY.test(data.error) ? data.error : '') || 'Հարցումը չհաջողվեց (կոդ ' + r.status + ')։');
        return data;
    }
    function show(data) {
        $('rmHistory').textContent = '';
        for (const r of (data.validation || []).slice().reverse()) {
            const tr = document.createElement('tr');
            const errors = Object.entries(r.metrics).filter(([,v]) => v.error_pct !== null)
                .map(([k,v]) => ({ km: 'կմ', minutes: 'ժամանակ', liters: 'դիզել', loading_minutes: 'բեռնում' }[k]) + '՝ ' + fmt(v.error_pct) + '%');
            const cells = [r.day + ' / ' + r.car_code, fmt(r.metrics.km.actual), fmt(r.metrics.minutes.actual), fmt(r.metrics.liters.actual),
                errors.length ? errors.join('; ') + (r.prospective ? '' : ' (հետին թվով)') : 'Պահպանված կանխատեսում չկա'];
            for (const text of cells) { const td = document.createElement('td'); td.textContent = text; tr.append(td); }
            $('rmHistory').append(tr);
        }
        if (!data.records) $('rmStatus').textContent = 'Չափումներ դեռ չկան։ Դատարկ արժեքները մնում են անհայտ։';
        $('rmSuggestions').textContent = '';
        for (const c of data.cars || []) {
            const p = document.createElement('p');
            p.textContent = c.car_code + '՝ ' + c.days + ' օր։ ';
            if (c.fuel) p.textContent += 'Ծախսն ըստ ստուգված չափումների․ դատարկ՝ ' + fmt(c.fuel.base) + ', լրիվ բեռնված՝ ' + fmt(c.fuel.base + c.fuel.slope) + ' լ/100 կմ։ ';
            else p.textContent += 'Ծախսի երկու նորմի համար անհրաժեշտ է ≥12 ամսաթիվ՝ տարբեր չափված բեռնվածությամբ, և վերջին 4 ամսաթվերի հաջող ստուգում։ ';
            if (c.loading) p.textContent += 'Բեռնում՝ ' + fmt(c.loading.base) + ' րոպե մեկ երթի համար + ' + fmt(c.loading.slope) + ' րոպե/տ։ ';
            if (c.maintenance_amd_per_km !== null) p.textContent += 'Տեխսպասարկումը դիտարկված ժամանակահատվածում՝ ' + fmt(c.maintenance_amd_per_km) + ' ֏/կմ․ բեռի ազդեցությունն առանձին հաստատված չէ։ ';
            $('rmSuggestions').append(p);
            if (c.fuel) {
                const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                btn.textContent = 'Տեղադրել ծախսի ստուգված նորմերը՝ ' + c.car_code;
                btn.addEventListener('click', () => {
                    const i = trucks.findIndex(t => t.car_code === c.car_code && !t.manual);
                    const row = i >= 0 ? document.querySelector('#rsTrucksBody tr[data-i="' + i + '"]') : null;
                    if (!row) { $('rmStatus').textContent = 'Մուտքագրեք առաջարկված նորմերը այս մեքենայի տողում և պահպանեք կարգավորումները։'; return; }
                    for (const [key, value] of [['fuel_empty_l_per_100km', c.fuel.base], ['fuel_full_l_per_100km', c.fuel.base + c.fuel.slope]]) {
                        const input = row.querySelector('[data-f="' + key + '"]');
                        if (input) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }
                    }
                    row.querySelector('details').open = true; row.scrollIntoView();
                    $('rmStatus').textContent = 'Նորմերը տեղադրված են։ Ստուգեք և սեղմեք կարգավորումների «Պահպանել» կոճակը։';
                });
                $('rmSuggestions').append(btn);
            }
            if (c.loading) {
                const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                btn.textContent = 'Տեղադրել բեռնման ժամանակը չափումներից՝ ' + c.car_code;
                btn.addEventListener('click', () => {
                    for (const [key, value] of [['warehouse_load_fixed_min', c.loading.base], ['warehouse_load_min_per_tonne', c.loading.slope]]) {
                        const input = document.querySelector('[data-norm="' + key + '"]');
                        if (input) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }
                    }
                    $('rsAuto').open = true; $('rsTrafficMode').scrollIntoView();
                    $('rmStatus').textContent = 'Նորմերը տեղադրված են։ Ստուգեք և սեղմեք կարգավորումների «Պահպանել» կոճակը։';
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
                ? 'Ծառայությունը միացված է։ Ապագա հերթափոխի համար օգտագործվում է երթևեկության կանխատեսումը դրա սկզբի համար, խափանման դեպքում՝ միջին արագությունը՝ հստակ նշումով «Առաքում» էջում։'
                : 'Ծառայության բանալին դեռ միացված չէ։ Հաշվարկն օգտագործում է միջին արագությունը, ընթացիկ խցանումները հայտնի չեն։';
            return;
        }
        $('rsTrafficStatus').textContent = report && report.hours_supported
            ? 'Ստուգված ժամային դասեր՝ ' + report.hours_supported + '։ Մենեջերների GPS-ը տալիս է արագության պատմական պրոֆիլ։ Ընթացիկ խցանումները հայտնի չեն։'
            : 'Ժամային պրոֆիլը դեռ չի անցել անկախ ստուգում՝ օգտագործվում է միջին արագությունը։ Ընթացիկ խցանումները հայտնի չեն։';
    });
    document.addEventListener('DOMContentLoaded', () => {
        const box = $('rmFields'); if (!box) return;
        const date = document.createElement('input'); date.type = 'date';
        const today = new Date(); date.value = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
        date.max = date.value; date.required = true;
        const car = document.createElement('select'); car.required = true;
        box.append(field('rmDay', 'Ամսաթիվ', date), field('rmCar', 'Մեքենա', car));
        for (const [key, label] of fields) { const input = document.createElement('input'); input.type = 'number'; input.min = 0; input.step = key === 'trips' ? '1' : 'any'; box.append(field('rm_' + key, label, input)); }
        $('rmForm').addEventListener('submit', async e => {
            e.preventDefault(); const button = e.currentTarget.querySelector('button[type="submit"]'); button.disabled = true;
            try {
                const body = { day: $('rmDay').value, car_code: $('rmCar').value };
                for (const [key] of fields) body[key] = $('rm_' + key).value === '' ? null : Number($('rm_' + key).value);
                show(await api('POST', body)); $('rmStatus').textContent = 'Չափումը պահպանվեց։';
            } catch (err) { $('rmStatus').textContent = err.message; }
            finally { button.disabled = false; }
        });
        api('GET').then(show).catch(err => { $('rmStatus').textContent = err.message; });
    });
})();

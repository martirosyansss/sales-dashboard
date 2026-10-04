/* «Обучение и факт» /routes/learning (docs/plans/learning-loop-plan.md, этапы 4–5).
   API: GET /api/routes/learning?from=&to= (план и факт по дням, что выучено), GET /api/routes/learning/status (лёгкий
   опрос во время пересчёта), GET /api/routes/learning/day?date=&car= (карта дня), POST /api/routes/learning/run
   (пересчитать в фоне), POST /api/routes/learning/auto {kind, auto}, POST /api/routes/road-lines (плановые рейсы по
   дорогам, avoid_center — в объезд малого центра, как их считает «Развоз»). «Время в пути грузовиков: модель» — строка вида truck_time в status (source: какая модель действует и
   почему; last.params.candidates — сравнение моделей). «Разгрузка по магазинам» (№50) — stores строки вида unload в status.
   Всё, что пришло с сервера, выводится только через esc() или
   textContent. Карта — Leaflet, как в «Развозе». */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const day = (s) => (typeof s === 'string' && s.length >= 10 ? s.slice(8, 10) + '.' + s.slice(5, 7) : '—');
    const hm = (m) => { const n = num(m); if (n === null) return '—'; const h = Math.floor(n / 60); return h + ' ժ ' + String(Math.round(n % 60)).padStart(2, '0') + ' ր'; };
    const clock = (m) => { const n = num(m); if (n === null) return ''; const h = Math.floor(n / 60) % 24, mm = Math.round(n % 60); return String(h).padStart(2, '0') + ':' + String(mm).padStart(2, '0'); };
    const isoDay = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
    const signed = (v) => { const n = num(v); return n === null ? '—' : (n > 0 ? '+' : '') + fmt(n, 2); };
    const modelName = (src) => (src === 'valhalla' ? 'Valhalla' : 'նախկին մոդել');
    const YEREVAN = [40.1792, 44.4991];
    let poll = null;

    function showError(text) {
        const box = $('lrAlert');
        if (!text) { box.classList.add('d-none'); return; }
        $('lrAlertText').textContent = text;
        box.classList.remove('d-none');
    }

    // Страница на армянском (решение владельца №58). Ответы не из раздела маршрутов (вход, доступ, CSRF — тексты дашборда
    // по-русски) — своим армянским текстом по коду ответа (глоссарий §1.13, как routes_garage.js); армянский текст сервера — как есть
    const HY = /[\u0531-\u058F]/;
    const HTTP_TEXT = {
        400: 'Սերվերը չընդունեց հարցումը։', 401: 'Անհրաժեշտ է մուտք գործել համակարգ։', 403: 'Մուտքն արգելված է — բաժինը միայն ադմինիստրատորի համար է։',
        404: 'Չի գտնվել։', 409: 'Վերահաշվարկն արդեն ընթանում է։', 415: 'Սերվերը չընդունեց հարցումը։', 500: 'Սերվերի ներքին սխալ։',
        503: 'ERP տվյալների բազան հասանելի չէ։',
    };
    async function api(url, json) {
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (json !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new Error('Սերվերի հետ կապ չկա։'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            if (resp.status === 403 && body && body.error === 'csrf') throw new Error('Էջը հնացել է՝ թարմացրեք այն և կրկնեք։');
            const text = body && typeof body.error === 'string' && HY.test(body.error) ? body.error : '';
            throw new Error(text || HTTP_TEXT[resp.status] || 'Սերվերի սխալ (կոդ ' + resp.status + ')։');
        }
        return body;
    }

    // ---------- Что выучено ----------
    function normText(kind, p) {
        if (!p) return null;
        if (kind === 'unload') {
            // №66: строка выбрала сглаживание к группе — своё время у всех магазинов с визитами (store_shrink)
            const shrink = !!p.store_rule && p.store_rule.rule === 'shrink';
            const n = Object.keys((shrink ? p.store_shrink : p.store_offsets) || {}).length;
            return fmt(p.per_stop_min, 1) + ' րոպե մեկ կետում + ' + fmt(p.per_tonne_min, 1) + ' րոպե յուրաքանչյուր տոննայի համար'
                + (n ? ' (սեփական ժամանակ՝ ' + fmt(n) + ' խանութում' + (shrink ? '՝ հարթեցում դեպի նման խանութները' : '') + ')' : '');
        }
        if (kind === 'loading') return fmt(p.fixed_min, 1) + ' րոպե մեկ երթի համար + ' + fmt(p.per_tonne_min, 1) + ' րոպե յուրաքանչյուր տոննայի համար';
        if (kind === 'travel') {
            const f = (p.factors || []).map(x => num(x[3])).filter(x => x !== null);
            if (!f.length) return 'ժամային ճշգրտում';
            return 'ժամային ճշգրտում ' + fmt(f.length) + ' ժամի համար՝ քարտեզի ժամանակը ×' + fmt(Math.min(...f), 2) + '-ից մինչև ×' + fmt(Math.max(...f), 2);
        }
        if (kind === 'fuel') return 'դատարկ՝ ' + fmt(p.empty_l100, 1) + ', լրիվ բեռնված՝ ' + fmt(p.full_l100, 1) + ' լ/100 կմ';
        if (kind === 'truck_time') return modelName(p.source);
        if (kind === 'lunch') return 'ճաշ՝ ' + fmt(p.minutes) + ' րոպե';   // обед в пути (№61)
        // запас на рейс (№66): c · √(минуты рейса), пример на типичном рейсе, покрытие на проверке
        if (kind === 'buffer') return 'պաշար՝ ' + fmt(p.c, 2) + ' × √(երթի րոպեներ), ' + fmt(p.q) + '%'
            + (num(p.typical_min) !== null ? ' (' + fmt(p.typical_min) + ' րոպե տևող երթին՝ +' + fmt(p.typical_reserve_min) + ' րոպե)' : '')
            + (num(p.coverage) !== null ? '․ ստուգման երթերից ժամանակին՝ ' + fmt(100 * p.coverage) + '%' : '');
        if (kind === 'truck_unload' || kind === 'truck_travel') {   // темп машины (№66)
            const f = p.factors || {}, cars = Object.keys(f).sort();
            return cars.length ? cars.map(c => c + ' ×' + fmt(f[c], 2)).join(', ') + ' (մյուսները՝ ×1)' : 'բոլոր մեքենաները՝ ×1';
        }
        return '';
    }
    function manualText(kind, m) {
        if (kind === 'unload' && m) return fmt(m.per_stop_min, 1) + ' րոպե մեկ կետում + ' + fmt(m.per_tonne_min, 1) + ' րոպե յուրաքանչյուր տոննայի համար';
        if (kind === 'loading') return m && num(m.fixed_min) !== null ? fmt(m.fixed_min, 1) + ' րոպե մեկ երթի համար + ' + fmt(m.per_tonne_min, 1) + ' րոպե յուրաքանչյուր տոննայի համար' : 'նշված չէ';
        if (kind === 'travel') return 'արագությունները՝ կարգավորումներից, խցանումները՝ ըստ մենեջերների GPS-ի';
        if (kind === 'truck_time') return modelName('model');
        if (kind === 'fuel' && m) return num(m.empty_l100) !== null ? 'դատարկ՝ ' + fmt(m.empty_l100, 1) + ', լրիվ բեռնված՝ ' + fmt(m.full_l100, 1) + ' լ/100 կմ' : fmt(m.l100, 1) + ' լ/100 կմ';
        if (kind === 'lunch' && m) return num(m.minutes) ? 'ճաշ՝ ' + fmt(m.minutes) + ' րոպե, սկիզբը՝ ' + m.from + '–' + m.to : 'ճաշն անջատված է';
        if (kind === 'buffer' && m) return num(m.q) > 50 ? 'պաշարը դեռ սովորած չէ (կարգավորումներում՝ ' + fmt(m.q) + '%)' : 'առանց պաշարի (50%)';
        if (kind === 'truck_unload' || kind === 'truck_travel') return 'բոլոր մեքենաները՝ ×1';
        return '—';
    }
    // Модель времени грузовиков: какая действует и почему (s.source от сервера: value, why — env | learned | default)
    function truckTimeWhy(s) {
        const src = s.source || {};
        if (src.why === 'env') return 'սահմանված է սերվերում ROUTES_TRUCK_TIME փոփոխականով — ծրագրի ընտրությունը չի գործում';
        if (src.why === 'learned') return 'ընտրել է ծրագիրը' + (s.in_effect ? '՝ ' + day(s.in_effect.run_day) : '');
        if (src.learned && !s.auto) return 'ուսուցումն անջատված է նշումով — լռելյայն՝ նախկին մոդել (ծրագիրն ընտրել էր՝ ' + modelName(src.learned) + ')';
        return 'լռելյայն՝ ծրագիրը դեռ չի ընտրել';
    }
    let firstFuel = null;
    function learnRow(s) {
        const title = esc(s.title) + (s.kind === 'travel' && s.scope ? ' · Valhalla-ի ժամանակի նկատմամբ' : s.scope ? ' · ' + esc(s.scope) : '');
        const eff = s.in_effect;
        const now = s.kind === 'truck_time'
            ? badge(modelName((s.source || {}).value), (s.source || {}).why === 'learned' ? 'b-ok' : 'b-none') + '<br><span class="lr-why">' + esc(truckTimeWhy(s)) + '</span>'
            : eff ? badge('սովորած՝ ' + day(eff.run_day), 'b-ok') + '<br>' + esc(normText(s.kind, eff.params))
                : badge('կարգավորումներից', 'b-none') + '<br>' + esc(manualText(s.kind, s.manual));
        const last = s.last;
        const learned = last && last.params && s.kind !== 'truck_time' ? '<br>Սովորած՝ ' + esc(normText(s.kind, last.params)) : '';
        const lastText = last ? (last.accepted ? badge('ընդունված է', 'b-ok') : badge('չի ընդունվել', 'b-warn')) + ' <span class="lr-why">'
            + esc(day(last.run_day)) + ' · տվյալներ՝ ' + fmt(last.n_obs) + ' + ստուգում՝ ' + fmt(last.n_test) + '<br>' + esc(last.reason) + learned + '</span>'
            : '<span class="lr-why">դեռ չի վերահաշվվել</span>';
        const err = last && num(last.mae_before) !== null ? fmt(last.mae_before, 2) + ' → ' + fmt(last.mae_after, 2) + (s.kind === 'fuel' ? ' լ/100 կմ' : ' րոպե') : '—';
        const off = !s.auto && !s.auto_chosen && !s.default_auto
            ? '<br><span class="lr-off">Լռելյայն անջատված է։ Ստուգեք, թե ինչ է սովորել ծրագիրը (ձախ սյունակում), և միացրեք ինքներդ։</span>' : '';
        const toggle = s.scope && s.kind === 'fuel' && s !== firstFuel ? '<span class="lr-why">ինչպես վերևի ծախսինը</span>'
            : '<label class="lr-why"><input type="checkbox" data-kind="' + esc(s.kind) + '"' + (s.auto ? ' checked' : '') + '> սովորել</label>' + off;
        return '<tr><th scope="row">' + title + '</th><td>' + now + '</td><td>' + lastText + '</td><td>' + esc(err) + '</td><td>' + toggle + '</td></tr>';
    }
    // Сравнение моделей времени грузовиков последнего пересчёта (params.candidates: ошибка и смещение без поправки и с ней)
    function renderTruckTime(s) {
        const last = s && s.last, p = last && last.params, c = p && p.candidates;
        $('lrTtNow').innerHTML = s ? 'Հիմա «Առաքում» էջում բեռնատարների ժամանակը հաշվվում է՝ <b>' + esc(modelName((s.source || {}).value)) + '</b> — ' + esc(truckTimeWhy(s)) + '։' : '';
        const line = (name, e) => '<tr><td>' + esc(name) + '</td><td data-label="Սխալ, րոպե մեկ հատվածի համար">' + fmt(e && e.mae, 2)
            + '</td><td data-label="Համակարգային շեղում, րոպե">' + signed(e && e.bias) + '</td></tr>';   // подписи — в узкой вёрстке (routes.css)
        $('lrTtRows').innerHTML = c && c.model && c.valhalla
            ? line('Նախկին մոդել (կմ / գոտու արագություն)', c.model.raw) + line('Նախկին մոդել + ժամային ճշգրտում', c.model.learned)
                + line('Valhalla', c.valhalla.raw) + line('Valhalla + ժամային ճշգրտում', c.valhalla.learned)
            : '<tr><td colspan="3" class="rt-empty">' + esc(last ? 'Համեմատություն դեռ չկա՝ ' + last.reason + '։' : 'Դեռ չի վերահաշվվել։') + '</td></tr>';
        const src = (s && s.source) || {};
        const changed = !last || !last.accepted ? ''
            : src.why === 'learned' ? 'Մոդելը փոխվեց՝ ' : 'Ծրագրի ընտրությունը փոխվեց, բայց հիմա չի գործում՝ ';
        $('lrTtLegs').textContent = p && p.legs && p.days
            ? 'Վերահաշվարկ ' + day(last.run_day) + '․ ուսուցման հատվածներ՝ ' + fmt(p.legs.train) + ' (' + fmt(p.days.train) + ' օր), ստուգման հատվածներ՝ '
                + fmt(p.legs.test) + ' (' + fmt(p.days.test) + ' օր)' + (num(p.legs.no_valhalla) ? ', առանց Valhalla-ի ժամանակի՝ ' + fmt(p.legs.no_valhalla) : '')
                + '։ ' + (p.corrected === false ? 'Արագության ժամային ուսուցումն անջատված է — մոդելները համեմատվում են առանց ճշգրտման։ ' : '')
                + changed + last.reason + '։'
            : '';
    }
    // Разгрузка по магазинам (№50): введено / по факту (визитов; split — 2 визита расходятся, ждём 3-й) / в расчёте
    // shrink — сглаживание к группе (№66): факт GPS вместе с временем похожих магазинов
    const STORE_SOURCE = { learned: 'ըստ GPS-ի', manual: 'ինչպես մուտքագրված է', norm: 'սովորական ժամանակ',
        shrink: 'GPS + նման խանութներ' };
    // По-армянски существительное после числа — в единственном числе: «2 բեռնաթափում», «5 բեռնաթափում»
    const unloads = (n) => fmt(n) + ' բեռնաթափում';
    // shrink — действует сглаживание к группе (№66): третьего не ждут; без введённого в расчёт идёт и 1-й визит, с
    // введённым до 2-го визита — введённое
    function storeRow(r, minVisits, shrink) {
        const name = r.name ? esc(r.name) + (r.code ? ' · ' + esc(r.code) : '') : 'հաճախորդ ' + esc(r.customer_id);
        const manual = num(r.manual_min) === null ? '—' : fmt(r.manual_min) + ' րոպե';
        const visits = num(r.visits);
        const fact = visits === null ? '<span class="lr-why">բեռնաթափումներ դեռ չկան</span>'
            : fmt(r.fact_min, 1) + ' րոպե <span class="lr-why">(' + unloads(visits)
                + (shrink ? (r.source === 'manual' ? ' — դեռ քիչ է, գործում է մուտքագրվածը' : '')
                    : r.split ? ' — շատ են տարբերվում, ծրագիրը սպասում է երրորդին'
                    : visits < minVisits ? ' — դեռ քիչ է, հաշվարկում չի մտնում' : '') + ')</span>';
        return '<tr><th scope="row">' + name + '</th><td>' + manual + '</td><td>' + fact + '</td><td><b>' + fmt(r.in_calc_min, 1)
            + ' րոպե</b><br><span class="lr-why">' + esc(STORE_SOURCE[r.source] || '') + '</span></td></tr>';
    }
    function renderStores(s) {
        const st = s && s.stores;
        if (!st) { $('lrStoreRows').innerHTML = '<tr><td colspan="4" class="rt-empty">Դեռ ցույց տալու բան չկա։</td></tr>'; $('lrStoresNote').textContent = ''; return; }
        $('lrStoresMin').textContent = st.min_visits;
        const shrink = st.rule === 'shrink';   // №66: текст правила — того, что действует
        $('lrStoresRule').hidden = shrink;
        $('lrStoresShrink').hidden = !shrink;
        $('lrRuleN60').hidden = shrink;
        $('lrRuleShrink').hidden = !shrink;
        $('lrStoresK').textContent = shrink ? fmt(st.k, 1) : '—';
        $('lrStoresTonne').textContent = fmt(st.per_tonne_min, 1);
        $('lrStoreRows').innerHTML = st.rows.length ? st.rows.map(r => storeRow(r, st.min_visits, shrink)).join('')
            : '<tr><td colspan="4" class="rt-empty">Դեռ ոչ մի խանութ սեփական ժամանակ չունի։ Այն կարելի է մուտքագրել «Կարգավորումներ → Խանութներ՝ ժամ և մեքենաներ» բաժնում․ '
                + 'ըստ փաստի այն կհայտնվի, երբ խանութում կկուտակվի ' + esc(unloads(shrink ? 1 : st.min_visits)) + '։</td></tr>';
        $('lrStoresNote').textContent = 'Մնացած խանութներում՝ սովորական ' + fmt(st.per_stop_min, 1) + ' րոպե։'
            + (st.run_day ? ' «Ըստ փաստի» սյունակը՝ ' + day(st.run_day) + '-ի վերահաշվարկից։' : '')
            + (st.total > st.shown ? ' Ցույց են տրված ' + fmt(st.shown) + ' / ' + fmt(st.total) + ' խանութ՝ ամենաշատ բեռնաթափումներով։' : '');
    }
    function renderStatus(d) {
        const status = d.status || [];
        firstFuel = status.find(s => s.kind === 'fuel') || null;
        renderTruckTime(status.find(s => s.kind === 'truck_time') || null);
        renderStores(status.find(s => s.kind === 'unload') || null);
        $('lrLearnRows').innerHTML = status.length ? status.map(learnRow).join('')
            : '<tr><td colspan="5" class="rt-empty">Դեռ ցույց տալու բան չկա։</td></tr>';
        const w = d.warning;
        $('lrWarn').hidden = !w;
        $('lrWarnText').textContent = w ? w.text + ' (' + String(w.at || '').slice(11, 16) + ')' : '';
        renderJob(d.job);
    }
    function renderJob(job) {
        const box = $('lrJob');
        if (!job || !job.status) { box.textContent = ''; $('lrRun').disabled = false; return; }
        if (job.status === 'running') box.textContent = 'Վերահաշվում եմ…' + (job.started_at ? ' (սկսվել է ժամը ' + job.started_at.slice(11, 16) + '-ին)' : '');
        else if (job.status === 'done') box.textContent = 'Վերահաշվվեց ժամը ' + (job.finished_at || '').slice(11, 16) + '-ին։';
        else box.textContent = 'Չհաջողվեց՝ ' + (job.error && HY.test(job.error) ? job.error : 'սերվերի սխալ');
        $('lrRun').disabled = job.status === 'running';
    }

    // ---------- План и факт ----------
    const pf = (fact, plan, d = 0, unit = '') => '<span class="lr-pf"><b>' + fmt(fact, d) + unit + '</b><span>պլան՝ ' + fmt(plan, d) + unit + '</span></span>';
    const pfTime = (fact, plan) => '<span class="lr-pf"><b>' + hm(fact) + '</b><span>պլան՝ ' + hm(plan) + '</span></span>';
    function dayRow(r) {
        const k = r.kpi, f = r.fact, p = r.plan;
        const inWindow = k.with_window ? fmt(k.on_time_pct) + '% <span class="lr-why">' + fmt(k.with_window) + '-ից' + (k.early ? ', ընդունման ժամից շուտ՝ ' + fmt(k.early) : '') + '</span>' : '—';
        const order = k.ordered ? fmt(k.order_changes) + ' / ' + fmt(k.ordered) : '—';
        return '<tr><td>' + esc(day(r.day)) + '</td><td>' + esc(r.car_code) + '</td><td>' + pf(f.km, p.km, 1) + '</td><td>' + pfTime(f.minutes, p.minutes)
            + '</td><td>' + pf(f.trips, p.trips) + '</td><td>' + pf(f.liters, p.liters, 1) + '</td><td>' + pf(f.stops, p.stops) + '</td><td>' + inWindow
            + '</td><td>' + fmt(k.stops_per_hour, 1) + '</td><td>' + fmt(k.km_per_stop, 1) + '</td><td>' + fmt(k.liters_per_stop, 2)
            + '</td><td>' + (num(k.load_pct) === null ? '—' : fmt(k.load_pct) + '%') + '</td><td>' + order
            + '</td><td><button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-day="' + esc(r.day) + '" data-car="' + esc(r.car_code) + '">Քարտեզ</button></td></tr>';
    }
    function kpi(label, value, sub) {
        return '<div class="rt-kpi"><div class="rt-kpi-label">' + esc(label) + '</div><div class="rt-kpi-val"><span class="now">' + value + '</span></div>'
            + '<div class="rt-kpi-sub">' + sub + '</div></div>';
    }
    function renderKpis(rows) {
        if (!rows.length) { $('lrKpis').innerHTML = ''; return; }
        const sum = (f) => rows.reduce((a, r) => a + (num(f(r)) || 0), 0);
        const km = sum(r => r.fact.km), kmPlan = sum(r => r.plan.km);
        const stops = sum(r => r.fact.stops), win = sum(r => r.kpi.with_window);
        const onTime = rows.reduce((a, r) => a + (r.kpi.with_window ? r.kpi.on_time_pct * r.kpi.with_window / 100 : 0), 0);
        const hours = sum(r => r.fact.minutes) / 60;
        $('lrKpis').innerHTML = kpi('Կմ ըստ GPS-ի', fmt(km), 'պլան՝ ' + fmt(kmPlan) + ' կմ · ' + fmt(rows.length) + ' մեքենա-օր')
            + kpi('Այցելած կետեր', fmt(stops), 'միջինում ' + (km && stops ? fmt(km / stops, 1) : '—') + ' կմ մեկ կետի համար')
            + kpi('Ժամանակին՝ ընդունման ժամին', win ? fmt(100 * onTime / win) + '%' : '—', win ? fmt(win) + ' կետից, որոնք ունեն ընդունման ժամ' : 'ընդունման ժամով կետեր չկային')
            + kpi('Կետ ժամում', hours ? fmt(stops / hours, 1) : '—', 'առաջին երթի մեկնումից մինչև վերջինի վերադարձը');
    }
    function render(d) {
        showError(!d.connected ? '«Առաքիչ» բաժինը (վարորդների տերմինալները) միացված չէ — փաստ չկա։'
            : !d.depot ? 'Պահեստը նշված չէ — նշեք այն կարգավորումներում, այլապես երթերը հնարավոր չէ գտնել։' : '');
        $('lrNightly').textContent = d.rules.nightly_at;
        $('lrHoldout').textContent = d.rules.holdout_days;
        $('lrGain').textContent = d.rules.min_gain_pct;
        $('lrTtGain').textContent = d.rules.min_gain_pct;
        $('lrFuelMin').textContent = d.rules.fuel_min_intervals;
        // устойчивость выигрыша (бутстреп) и пороги обеда — числа правил с сервера, не разбор текста причин
        [['lrBootShare', d.rules.boot_share_pct], ['lrBootN', d.rules.boot_resamples],
            ['lrLunchTrain', (d.rules.lunch_min || [])[0]], ['lrLunchTest', (d.rules.lunch_min || [])[2]]].forEach(([id, v]) => {
            if (num(v) !== null && $(id)) $(id).textContent = v;
        });
        const tt = d.rules.truck_time_min || [];   // [участков обучения, дней обучения, участков проверки, дней проверки]
        [['lrTtTrain', 0], ['lrTtTrainDays', 1], ['lrTtTest', 2], ['lrTtTestDays', 3]].forEach(([id, i]) => {
            if (num(tt[i]) !== null) $(id).textContent = tt[i];
        });
        renderStatus(d);
        renderKpis(d.days);
        $('lrDayRows').innerHTML = d.days.length ? d.days.map(dayRow).join('')
            : '<tr><td colspan="14" class="rt-empty">Այս օրերի համար մեքենաների հետագիծ չկա։ Այն կհայտնվի, երբ վարորդները սկսեն աշխատել տերմինալի նոր տարբերակով (երթուղու գրանցում)։</td></tr>';
        if (d.job && d.job.status === 'running') schedulePoll();
    }
    async function load() {
        try {
            const q = '?from=' + encodeURIComponent($('lrFrom').value) + '&to=' + encodeURIComponent($('lrTo').value);
            render(await api('/api/routes/learning' + q));
            $('lrStatus').textContent = 'Թարմացված է';
        } catch (e) { showError(e.message); }
    }
    // Во время пересчёта опрашивается только лёгкий статус; по окончании — один раз весь отчёт
    function schedulePoll() {
        if (poll) return;
        poll = setTimeout(async () => {
            poll = null;
            let d;
            try { d = await api('/api/routes/learning/status'); } catch (e) { showError(e.message); return; }
            renderStatus(d);
            if (d.job && d.job.status === 'running') schedulePoll(); else load();
        }, 3000);
    }
    async function run() {
        $('lrRun').disabled = true;
        try {
            await api('/api/routes/learning/run', {});
            renderJob({ status: 'running' });
            schedulePoll();
        } catch (e) { showError(e.message); $('lrRun').disabled = false; }
    }
    async function toggle(ev) {
        const box = ev.target.closest('input[data-kind]');
        if (!box) return;
        box.disabled = true;
        try { renderStatus(await api('/api/routes/learning/auto', { kind: box.dataset.kind, auto: box.checked })); }
        catch (e) { showError(e.message); box.checked = !box.checked; box.disabled = false; }
    }

    // ---------- Карта дня «план — факт» (ответ владельца №46) ----------
    const map = { obj: null, layers: null, failed: false, gen: 0 };
    function ensureMap() {
        if (map.obj || map.failed) return;
        const el = $('lrMap');
        if (typeof window.L === 'undefined') {
            map.failed = true;
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ)։ Ներքևի կետերի աղյուսակն աշխատում է։';
            return;
        }
        map.obj = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, zoomControl: false });
        L.control.zoom({ zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' }).addTo(map.obj);   // подсказки кнопок — по-армянски (как в настройках)
        RoutesBasemap.add(map.obj);
        map.obj.setView(YEREVAN, 11);
        map.obj.on('click focus', () => map.obj.scrollWheelZoom.enable());
        map.obj.on('mouseout blur', () => map.obj.scrollWheelZoom.disable());
        map.layers = L.layerGroup().addTo(map.obj);
    }
    function stopColor(s) {
        if (!s.arrive) return '#8791a3';
        return (num(s.late_min) > 0 || s.early) ? '#ff6b79' : '#45d98f';
    }
    function windowText(w) {
        if (!w) return '';
        if (w[0] === null) return 'մինչև ' + clock(w[1]);
        if (w[1] === null) return clock(w[0]) + '-ից հետո';
        return clock(w[0]) + '–' + clock(w[1]);
    }
    function stopFact(s) {
        if (!s.arrive) return 'կանգառ չի եղել';
        const late = num(s.late_min) > 0 ? ', ուշացում՝ ' + fmt(s.late_min) + ' րոպե' : '';
        return s.arrive + '–' + s.leave + late + (s.early ? ', ընդունման ժամից շուտ' : '');
    }
    async function showDay(dayIso, car) {
        $('lrMapBox').hidden = false;
        $('lrMapTitle').textContent = car + ' · ' + day(dayIso);
        $('lrMapStops').innerHTML = '<tr><td colspan="5" class="rt-empty">Բեռնվում է…</td></tr>';
        $('lrMapBox').scrollIntoView({ behavior: 'smooth', block: 'start' });
        ensureMap();
        const gen = ++map.gen;
        let d;
        try { d = await api('/api/routes/learning/day?date=' + encodeURIComponent(dayIso) + '&car=' + encodeURIComponent(car)); }
        catch (e) { showError(e.message); return; }
        if (gen !== map.gen) return;
        $('lrMapNote').textContent = 'GPS կետեր՝ ' + fmt(d.track_points) + ' (քարտեզում՝ ' + fmt(d.track.length) + '), շարժման կմ՝ ' + fmt(d.km_gps, 1)
            + (d.trips.length ? '։ Երթեր՝ ' + d.trips.map((t, i) => (i + 1) + ') ' + (t.depart || '?') + '–' + (t.return || '?')
                + (t.load_min !== null ? ', պահեստում՝ ' + fmt(t.load_min) + ' րոպե' : '')).join('; ') : '');
        const rows = d.stops.slice().sort((a, b) => (num(a.rank) ?? 1e9) - (num(b.rank) ?? 1e9));
        $('lrMapStops').innerHTML = rows.length ? rows.map(s => '<tr><td>' + (num(s.rank) === null ? '—' : fmt(s.rank + 1)) + '</td><td>' + esc(s.name || s.customer_id || s.stop_id)
            + '</td><td>' + esc(s.planned_eta || '—') + '</td><td>' + esc(windowText(s.window) || '—') + '</td><td>'
            + '<span class="rt-dot" style="background:' + stopColor(s) + '"></span> ' + esc(stopFact(s)) + '</td></tr>').join('')
            : '<tr><td colspan="5" class="rt-empty">Օրվա կետեր չկան։</td></tr>';
        if (!map.obj) return;
        map.obj.invalidateSize();
        map.layers.clearLayers();
        const bounds = [];
        const planned = d.planned.map(line => {
            bounds.push(...line);
            return [L.polyline(line, { color: '#8791a3', weight: 3, opacity: .85, dashArray: '6 6' }).addTo(map.layers), line];
        });
        if (d.track.length > 1) {
            L.polyline(d.track, { color: '#3b82f6', weight: 3, opacity: .85 }).bindTooltip('Փաստացի ճանապարհ (GPS)').addTo(map.layers);
            bounds.push(...d.track);
        }
        d.stops.forEach(s => {
            if (s.lat === null) return;
            bounds.push([s.lat, s.lon]);
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: stopColor(s), fillOpacity: 1 })
                .bindTooltip(esc(s.name || s.customer_id || '') + '<br>պլան՝ ' + esc(s.planned_eta || '—') + ' · փաստ՝ ' + esc(stopFact(s))
                    + (s.window ? '<br>ընդունման ժամ՝ ' + esc(windowText(s.window)) : '')).addTo(map.layers);
        });
        if (d.depot) {
            bounds.push(d.depot);
            L.marker(d.depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<span><i class="fas fa-warehouse" aria-hidden="true"></i></span>', iconSize: [28, 28], iconAnchor: [14, 14] }), keyboard: false, zIndexOffset: 1000 })
                .bindTooltip('Պահեստ').addTo(map.layers);
        }
        if (bounds.length) map.obj.fitBounds(bounds, { padding: [24, 24], maxZoom: 15, animate: false });
        roadLines(planned, gen);
    }
    // Плановые рейсы сначала по прямой, затем — по дорогам, как у «Развоза» (в объезд малого центра, как считаются км
    // плана); нет карты дорог — прямые
    async function roadLines(planned, gen) {
        if (!planned.length) return;
        let data;
        try { data = await api('/api/routes/road-lines', { lines: planned.map(([, line]) => line), avoid_center: true }); } catch (e) { return; }
        if (gen !== map.gen || !Array.isArray(data.lines)) return;
        planned.forEach(([pl], i) => { const road = data.lines[i]; if (Array.isArray(road) && road.length > 1) pl.setLatLngs(road); });
    }

    function init() {
        const y = new Date(); y.setDate(y.getDate() - 1);
        const from = new Date(y); from.setDate(from.getDate() - 13);
        $('lrTo').value = isoDay(y);
        $('lrFrom').value = isoDay(from);
        $('lrPeriod').addEventListener('submit', (ev) => { ev.preventDefault(); load(); });
        $('lrRun').addEventListener('click', run);
        $('lrLearnRows').addEventListener('change', toggle);
        $('lrDayRows').addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-day]');
            if (b) showDay(b.dataset.day, b.dataset.car);
        });
        load();
    }
    document.addEventListener('DOMContentLoaded', init);
})();

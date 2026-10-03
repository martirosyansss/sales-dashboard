/* «Обучение и факт» /routes/learning (docs/plans/learning-loop-plan.md, этапы 4–5).
   API: GET /api/routes/learning?from=&to= (план и факт по дням, что выучено), GET /api/routes/learning/status (лёгкий
   опрос во время пересчёта), GET /api/routes/learning/day?date=&car= (карта дня), POST /api/routes/learning/run
   (пересчитать в фоне), POST /api/routes/learning/auto {kind, auto}, POST /api/routes/road-lines (плановые рейсы по
   дорогам, avoid_center — в объезд малого центра, как их считает «Развоз»). «Время в пути грузовиков: модель» — строка вида truck_time в status (source: какая модель действует и
   почему; last.params.candidates — сравнение моделей). Всё, что пришло с сервера, выводится только через esc() или
   textContent. Карта — Leaflet, как в «Развозе». */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const day = (s) => (typeof s === 'string' && s.length >= 10 ? s.slice(8, 10) + '.' + s.slice(5, 7) : '—');
    const hm = (m) => { const n = num(m); if (n === null) return '—'; const h = Math.floor(n / 60); return h + ' ч ' + String(Math.round(n % 60)).padStart(2, '0') + ' мин'; };
    const clock = (m) => { const n = num(m); if (n === null) return ''; const h = Math.floor(n / 60) % 24, mm = Math.round(n % 60); return String(h).padStart(2, '0') + ':' + String(mm).padStart(2, '0'); };
    const isoDay = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
    const signed = (v) => { const n = num(v); return n === null ? '—' : (n > 0 ? '+' : '') + fmt(n, 2); };
    const modelName = (src) => (src === 'valhalla' ? 'Valhalla' : 'прежняя модель');
    const YEREVAN = [40.1792, 44.4991];
    let poll = null;

    function showError(text) {
        const box = $('lrAlert');
        if (!text) { box.classList.add('d-none'); return; }
        $('lrAlertText').textContent = text;
        box.classList.remove('d-none');
    }

    async function api(url, json) {
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (json !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new Error('Сервер недоступен'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            throw new Error(body && body.error ? body.error : 'Ошибка сервера (' + resp.status + ')');
        }
        return body;
    }

    // ---------- Что выучено ----------
    function normText(kind, p) {
        if (!p) return null;
        if (kind === 'unload') {
            const n = Object.keys(p.store_offsets || {}).length;
            return fmt(p.per_stop_min, 1) + ' мин на точку + ' + fmt(p.per_tonne_min, 1) + ' мин на тонну' + (n ? ' (свои поправки у ' + fmt(n) + ' магазинов)' : '');
        }
        if (kind === 'loading') return fmt(p.fixed_min, 1) + ' мин на рейс + ' + fmt(p.per_tonne_min, 1) + ' мин на тонну';
        if (kind === 'travel') {
            const f = (p.factors || []).map(x => num(x[3])).filter(x => x !== null);
            if (!f.length) return 'поправка по часам';
            return 'поправка по ' + fmt(f.length) + ' часам: от ×' + fmt(Math.min(...f), 2) + ' до ×' + fmt(Math.max(...f), 2) + ' ко времени по карте';
        }
        if (kind === 'fuel') return 'пустая ' + fmt(p.empty_l100, 1) + ', полная ' + fmt(p.full_l100, 1) + ' л/100 км';
        if (kind === 'truck_time') return modelName(p.source);
        return '';
    }
    function manualText(kind, m) {
        if (kind === 'unload' && m) return fmt(m.per_stop_min, 1) + ' мин на точку + ' + fmt(m.per_tonne_min, 1) + ' мин на тонну';
        if (kind === 'loading') return m && num(m.fixed_min) !== null ? fmt(m.fixed_min, 1) + ' мин на рейс + ' + fmt(m.per_tonne_min, 1) + ' мин на тонну' : 'не задана';
        if (kind === 'travel') return 'скорости из настроек и пробки по GPS менеджеров';
        if (kind === 'truck_time') return modelName('model');
        if (kind === 'fuel' && m) return num(m.empty_l100) !== null ? 'пустая ' + fmt(m.empty_l100, 1) + ', полная ' + fmt(m.full_l100, 1) + ' л/100 км' : fmt(m.l100, 1) + ' л/100 км';
        return '—';
    }
    // Модель времени грузовиков: какая действует и почему (s.source от сервера: value, why — env | learned | default)
    function truckTimeWhy(s) {
        const src = s.source || {};
        if (src.why === 'env') return 'задано на сервере переменной ROUTES_TRUCK_TIME — выбор программы не действует';
        if (src.why === 'learned') return 'выбрано программой' + (s.in_effect ? ' ' + day(s.in_effect.run_day) : '');
        if (src.learned && !s.auto) return 'учёба выключена галочкой — по умолчанию прежняя модель (программа выбрала: ' + modelName(src.learned) + ')';
        return 'по умолчанию: программа ещё не выбирала';
    }
    let firstFuel = null;
    function learnRow(s) {
        const title = esc(s.title) + (s.kind === 'travel' && s.scope ? ' · к времени Valhalla' : s.scope ? ' · ' + esc(s.scope) : '');
        const eff = s.in_effect;
        const now = s.kind === 'truck_time'
            ? badge(modelName((s.source || {}).value), (s.source || {}).why === 'learned' ? 'b-ok' : 'b-none') + '<br><span class="lr-why">' + esc(truckTimeWhy(s)) + '</span>'
            : eff ? badge('выучено ' + day(eff.run_day), 'b-ok') + '<br>' + esc(normText(s.kind, eff.params))
                : badge('из настроек', 'b-none') + '<br>' + esc(manualText(s.kind, s.manual));
        const last = s.last;
        const learned = last && last.params && s.kind !== 'truck_time' ? '<br>Выучено: ' + esc(normText(s.kind, last.params)) : '';
        const lastText = last ? (last.accepted ? badge('принято', 'b-ok') : badge('не принято', 'b-warn')) + ' <span class="lr-why">'
            + esc(day(last.run_day)) + ' · данных ' + fmt(last.n_obs) + ' + проверка ' + fmt(last.n_test) + '<br>' + esc(last.reason) + learned + '</span>'
            : '<span class="lr-why">ещё не пересчитывалось</span>';
        const err = last && num(last.mae_before) !== null ? fmt(last.mae_before, 2) + ' → ' + fmt(last.mae_after, 2) + (s.kind === 'fuel' ? ' л/100 км' : ' мин') : '—';
        const off = !s.auto && !s.auto_chosen && !s.default_auto
            ? '<br><span class="lr-off">По умолчанию выключено: проверьте, что выучено (колонка слева), и включите сами.</span>' : '';
        const toggle = s.scope && s.kind === 'fuel' && s !== firstFuel ? '<span class="lr-why">как у расхода выше</span>'
            : '<label class="lr-why"><input type="checkbox" data-kind="' + esc(s.kind) + '"' + (s.auto ? ' checked' : '') + '> учиться</label>' + off;
        return '<tr><th scope="row">' + title + '</th><td>' + now + '</td><td>' + lastText + '</td><td>' + esc(err) + '</td><td>' + toggle + '</td></tr>';
    }
    // Сравнение моделей времени грузовиков последнего пересчёта (params.candidates: ошибка и смещение без поправки и с ней)
    function renderTruckTime(s) {
        const last = s && s.last, p = last && last.params, c = p && p.candidates;
        $('lrTtNow').innerHTML = s ? 'Сейчас «Развоз» считает грузовики: <b>' + esc(modelName((s.source || {}).value)) + '</b> — ' + esc(truckTimeWhy(s)) + '.' : '';
        const line = (name, e) => '<tr><td>' + esc(name) + '</td><td data-label="Ошибка, мин на участок">' + fmt(e && e.mae, 2)
            + '</td><td data-label="Смещение, мин">' + signed(e && e.bias) + '</td></tr>';   // подписи — в узкой вёрстке (routes.css)
        $('lrTtRows').innerHTML = c && c.model && c.valhalla
            ? line('Прежняя модель (км / скорость зоны)', c.model.raw) + line('Прежняя модель + поправка по часам', c.model.learned)
                + line('Valhalla', c.valhalla.raw) + line('Valhalla + поправка по часам', c.valhalla.learned)
            : '<tr><td colspan="3" class="rt-empty">' + esc(last ? 'Сравнения пока нет: ' + last.reason + '.' : 'Ещё не пересчитывалось.') + '</td></tr>';
        const src = (s && s.source) || {};
        const changed = !last || !last.accepted ? ''
            : src.why === 'learned' ? 'Модель сменилась: ' : 'Выбор программы сменился, но сейчас не действует: ';
        $('lrTtLegs').textContent = p && p.legs && p.days
            ? 'Пересчёт ' + day(last.run_day) + ': участков на обучении ' + fmt(p.legs.train) + ' (дней ' + fmt(p.days.train) + '), на проверке '
                + fmt(p.legs.test) + ' (дней ' + fmt(p.days.test) + ')' + (num(p.legs.no_valhalla) ? '; без времени Valhalla — ' + fmt(p.legs.no_valhalla) : '')
                + '. ' + (p.corrected === false ? 'Учёба скорости по часам выключена — модели сравниваются без поправки. ' : '')
                + changed + last.reason + '.'
            : '';
    }
    function renderStatus(d) {
        const status = d.status || [];
        firstFuel = status.find(s => s.kind === 'fuel') || null;
        renderTruckTime(status.find(s => s.kind === 'truck_time') || null);
        $('lrLearnRows').innerHTML = status.length ? status.map(learnRow).join('')
            : '<tr><td colspan="5" class="rt-empty">Пока нечего показать.</td></tr>';
        const w = d.warning;
        $('lrWarn').hidden = !w;
        $('lrWarnText').textContent = w ? w.text + ' (' + String(w.at || '').slice(11, 16) + ')' : '';
        renderJob(d.job);
    }
    function renderJob(job) {
        const box = $('lrJob');
        if (!job || !job.status) { box.textContent = ''; $('lrRun').disabled = false; return; }
        if (job.status === 'running') box.textContent = 'Пересчитываю…' + (job.started_at ? ' (начато в ' + job.started_at.slice(11, 16) + ')' : '');
        else if (job.status === 'done') box.textContent = 'Пересчитано в ' + (job.finished_at || '').slice(11, 16) + '.';
        else box.textContent = 'Не получилось: ' + (job.error || 'ошибка');
        $('lrRun').disabled = job.status === 'running';
    }

    // ---------- План и факт ----------
    const pf = (fact, plan, d = 0, unit = '') => '<span class="lr-pf"><b>' + fmt(fact, d) + unit + '</b><span>план ' + fmt(plan, d) + unit + '</span></span>';
    const pfTime = (fact, plan) => '<span class="lr-pf"><b>' + hm(fact) + '</b><span>план ' + hm(plan) + '</span></span>';
    function dayRow(r) {
        const k = r.kpi, f = r.fact, p = r.plan;
        const inWindow = k.with_window ? fmt(k.on_time_pct) + '% <span class="lr-why">из ' + fmt(k.with_window) + (k.early ? ', раньше окна ' + fmt(k.early) : '') + '</span>' : '—';
        const order = k.ordered ? fmt(k.order_changes) + ' из ' + fmt(k.ordered) : '—';
        return '<tr><td>' + esc(day(r.day)) + '</td><td>' + esc(r.car_code) + '</td><td>' + pf(f.km, p.km, 1) + '</td><td>' + pfTime(f.minutes, p.minutes)
            + '</td><td>' + pf(f.trips, p.trips) + '</td><td>' + pf(f.liters, p.liters, 1) + '</td><td>' + pf(f.stops, p.stops) + '</td><td>' + inWindow
            + '</td><td>' + fmt(k.stops_per_hour, 1) + '</td><td>' + fmt(k.km_per_stop, 1) + '</td><td>' + fmt(k.liters_per_stop, 2)
            + '</td><td>' + (num(k.load_pct) === null ? '—' : fmt(k.load_pct) + '%') + '</td><td>' + order
            + '</td><td><button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-day="' + esc(r.day) + '" data-car="' + esc(r.car_code) + '">Карта</button></td></tr>';
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
        $('lrKpis').innerHTML = kpi('Км по GPS', fmt(km), 'план ' + fmt(kmPlan) + ' км · ' + fmt(rows.length) + ' машино-дней')
            + kpi('Точек объехано', fmt(stops), 'в среднем ' + (km && stops ? fmt(km / stops, 1) : '—') + ' км на точку')
            + kpi('Вовремя к окну', win ? fmt(100 * onTime / win) + '%' : '—', win ? 'из ' + fmt(win) + ' точек с окном приёма' : 'точек с окном не было')
            + kpi('Точек в час', hours ? fmt(stops / hours, 1) : '—', 'от выезда первого рейса до возвращения последнего');
    }
    function render(d) {
        showError(!d.connected ? 'Раздел «Առաքիչ» (терминалы водителей) не подключён — факта нет.'
            : !d.depot ? 'Не указан склад — укажите его в настройках, иначе рейсы не найти.' : '');
        $('lrNightly').textContent = d.rules.nightly_at;
        $('lrHoldout').textContent = d.rules.holdout_days;
        $('lrGain').textContent = d.rules.min_gain_pct;
        $('lrTtGain').textContent = d.rules.min_gain_pct;
        $('lrFuelMin').textContent = d.rules.fuel_min_intervals;
        const tt = d.rules.truck_time_min || [];   // [участков обучения, дней обучения, участков проверки, дней проверки]
        [['lrTtTrain', 0], ['lrTtTrainDays', 1], ['lrTtTest', 2], ['lrTtTestDays', 3]].forEach(([id, i]) => {
            if (num(tt[i]) !== null) $(id).textContent = tt[i];
        });
        renderStatus(d);
        renderKpis(d.days);
        $('lrDayRows').innerHTML = d.days.length ? d.days.map(dayRow).join('')
            : '<tr><td colspan="14" class="rt-empty">За эти дни трека машин нет. Он появится, когда водители начнут работать с новой версией терминала (запись маршрута).</td></tr>';
        if (d.job && d.job.status === 'running') schedulePoll();
    }
    async function load() {
        try {
            const q = '?from=' + encodeURIComponent($('lrFrom').value) + '&to=' + encodeURIComponent($('lrTo').value);
            render(await api('/api/routes/learning' + q));
            $('lrStatus').textContent = 'Обновлено';
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
            el.textContent = 'Карта не загрузилась (cdn.jsdelivr.net недоступен). Таблица точек ниже работает.';
            return;
        }
        map.obj = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false });
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
        if (w[0] === null) return 'до ' + clock(w[1]);
        if (w[1] === null) return 'после ' + clock(w[0]);
        return clock(w[0]) + '–' + clock(w[1]);
    }
    function stopFact(s) {
        if (!s.arrive) return 'стоянки не было';
        const late = num(s.late_min) > 0 ? ', опоздание ' + fmt(s.late_min) + ' мин' : '';
        return s.arrive + '–' + s.leave + late + (s.early ? ', раньше окна' : '');
    }
    async function showDay(dayIso, car) {
        $('lrMapBox').hidden = false;
        $('lrMapTitle').textContent = car + ' · ' + day(dayIso);
        $('lrMapStops').innerHTML = '<tr><td colspan="5" class="rt-empty">Загружаю…</td></tr>';
        $('lrMapBox').scrollIntoView({ behavior: 'smooth', block: 'start' });
        ensureMap();
        const gen = ++map.gen;
        let d;
        try { d = await api('/api/routes/learning/day?date=' + encodeURIComponent(dayIso) + '&car=' + encodeURIComponent(car)); }
        catch (e) { showError(e.message); return; }
        if (gen !== map.gen) return;
        $('lrMapNote').textContent = 'Точек GPS: ' + fmt(d.track_points) + ' (на карте — ' + fmt(d.track.length) + '), км движения: ' + fmt(d.km_gps, 1)
            + (d.trips.length ? '. Рейсы: ' + d.trips.map((t, i) => (i + 1) + ') ' + (t.depart || '?') + '–' + (t.return || '?')
                + (t.load_min !== null ? ', на складе ' + fmt(t.load_min) + ' мин' : '')).join('; ') : '');
        const rows = d.stops.slice().sort((a, b) => (num(a.rank) ?? 1e9) - (num(b.rank) ?? 1e9));
        $('lrMapStops').innerHTML = rows.length ? rows.map(s => '<tr><td>' + (num(s.rank) === null ? '—' : fmt(s.rank + 1)) + '</td><td>' + esc(s.name || s.customer_id || s.stop_id)
            + '</td><td>' + esc(s.planned_eta || '—') + '</td><td>' + esc(windowText(s.window) || '—') + '</td><td>'
            + '<span class="rt-dot" style="background:' + stopColor(s) + '"></span> ' + esc(stopFact(s)) + '</td></tr>').join('')
            : '<tr><td colspan="5" class="rt-empty">Точек дня нет.</td></tr>';
        if (!map.obj) return;
        map.obj.invalidateSize();
        map.layers.clearLayers();
        const bounds = [];
        const planned = d.planned.map(line => {
            bounds.push(...line);
            return [L.polyline(line, { color: '#8791a3', weight: 3, opacity: .85, dashArray: '6 6' }).addTo(map.layers), line];
        });
        if (d.track.length > 1) {
            L.polyline(d.track, { color: '#3b82f6', weight: 3, opacity: .85 }).bindTooltip('Фактический путь (GPS)').addTo(map.layers);
            bounds.push(...d.track);
        }
        d.stops.forEach(s => {
            if (s.lat === null) return;
            bounds.push([s.lat, s.lon]);
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: stopColor(s), fillOpacity: 1 })
                .bindTooltip(esc(s.name || s.customer_id || '') + '<br>план ' + esc(s.planned_eta || '—') + ' · факт ' + esc(stopFact(s))
                    + (s.window ? '<br>окно ' + esc(windowText(s.window)) : '')).addTo(map.layers);
        });
        if (d.depot) {
            bounds.push(d.depot);
            L.marker(d.depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<i class="fas fa-warehouse"></i>', iconSize: [28, 28] }), keyboard: false })
                .bindTooltip('Склад').addTo(map.layers);
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

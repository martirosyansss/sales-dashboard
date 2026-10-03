/* «Обучение и факт» /routes/learning (docs/plans/learning-loop-plan.md, этапы 4–5).
   API: GET /api/routes/learning?from=&to= (план и факт по дням, что выучено), POST /api/routes/learning/run (пересчитать в фоне),
   POST /api/routes/learning/auto {kind, auto}. Всё, что пришло с сервера, выводится только через esc() или textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const day = (s) => (typeof s === 'string' && s.length >= 10 ? s.slice(8, 10) + '.' + s.slice(5, 7) : '—');
    const hm = (m) => { const n = num(m); if (n === null) return '—'; const h = Math.floor(n / 60); return h + ' ч ' + String(Math.round(n % 60)).padStart(2, '0') + ' мин'; };
    const isoDay = (d) => { const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset()); return x.toISOString().slice(0, 10); };
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
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
        return '';
    }
    function manualText(kind, m) {
        if (kind === 'unload' && m) return fmt(m.per_stop_min, 1) + ' мин на точку + ' + fmt(m.per_tonne_min, 1) + ' мин на тонну';
        if (kind === 'loading') return m && num(m.fixed_min) !== null ? fmt(m.fixed_min, 1) + ' мин на рейс + ' + fmt(m.per_tonne_min, 1) + ' мин на тонну' : 'не задана';
        if (kind === 'travel') return 'скорости из настроек и пробки по GPS менеджеров';
        if (kind === 'fuel' && m) return num(m.empty_l100) !== null ? 'пустая ' + fmt(m.empty_l100, 1) + ', полная ' + fmt(m.full_l100, 1) + ' л/100 км' : fmt(m.l100, 1) + ' л/100 км';
        return '—';
    }
    function learnRow(s) {
        const title = esc(s.title) + (s.scope ? ' · ' + esc(s.scope) : '');
        const eff = s.in_effect;
        const now = eff ? badge('выучено ' + day(eff.run_day), 'b-ok') + '<br>' + esc(normText(s.kind, eff.params))
            : badge('из настроек', 'b-none') + '<br>' + esc(manualText(s.kind, s.manual));
        const last = s.last;
        const lastText = last ? (last.accepted ? badge('принято', 'b-ok') : badge('не принято', 'b-warn')) + ' <span class="lr-why">'
            + esc(day(last.run_day)) + ' · данных ' + fmt(last.n_obs) + ' + проверка ' + fmt(last.n_test) + '<br>' + esc(last.reason) + '</span>'
            : '<span class="lr-why">ещё не пересчитывалось</span>';
        const err = last && num(last.mae_before) !== null ? fmt(last.mae_before, 2) + ' → ' + fmt(last.mae_after, 2) + (s.kind === 'fuel' ? ' л/100 км' : ' мин') : '—';
        const toggle = s.scope && s.kind === 'fuel' && s !== firstFuel ? '<span class="lr-why">как у расхода выше</span>'
            : '<label class="lr-why"><input type="checkbox" data-kind="' + esc(s.kind) + '"' + (s.auto ? ' checked' : '') + '> учиться</label>';
        return '<tr><th scope="row">' + title + '</th><td>' + now + '</td><td>' + lastText + '</td><td>' + esc(err) + '</td><td>' + toggle + '</td></tr>';
    }
    let firstFuel = null;
    function renderStatus(status) {
        firstFuel = status.find(s => s.kind === 'fuel') || null;
        $('lrLearnRows').innerHTML = status.length ? status.map(learnRow).join('')
            : '<tr><td colspan="5" class="rt-empty">Пока нечего показать.</td></tr>';
    }
    function renderJob(job) {
        const box = $('lrJob');
        if (!job || !job.status) { box.textContent = ''; return; }
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
        const inWindow = k.with_window ? fmt(k.on_time_pct) + '% <span class="lr-why">из ' + fmt(k.with_window) + '</span>' : '—';
        const order = k.ordered ? fmt(k.order_changes) + ' из ' + fmt(k.ordered) : '—';
        return '<tr><td>' + esc(day(r.day)) + '</td><td>' + esc(r.car_code) + '</td><td>' + pf(f.km, p.km, 1) + '</td><td>' + pfTime(f.minutes, p.minutes)
            + '</td><td>' + pf(f.trips, p.trips) + '</td><td>' + pf(f.liters, p.liters, 1) + '</td><td>' + pf(f.stops, p.stops) + '</td><td>' + inWindow
            + '</td><td>' + fmt(k.stops_per_hour, 1) + '</td><td>' + fmt(k.km_per_stop, 1) + '</td><td>' + fmt(k.liters_per_stop, 2)
            + '</td><td>' + (num(k.load_pct) === null ? '—' : fmt(k.load_pct) + '%') + '</td><td>' + order + '</td></tr>';
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
        $('lrFuelMin').textContent = d.rules.fuel_min_intervals;
        renderStatus(d.status);
        renderJob(d.job);
        renderKpis(d.days);
        $('lrDayRows').innerHTML = d.days.length ? d.days.map(dayRow).join('')
            : '<tr><td colspan="13" class="rt-empty">За эти дни трека машин нет. Он появится, когда водители начнут работать с новой версией терминала (запись маршрута).</td></tr>';
        if (d.job && d.job.status === 'running') schedulePoll();
    }
    async function load() {
        try {
            const q = '?from=' + encodeURIComponent($('lrFrom').value) + '&to=' + encodeURIComponent($('lrTo').value);
            render(await api('/api/routes/learning' + q));
            $('lrStatus').textContent = 'Обновлено';
        } catch (e) { showError(e.message); }
    }
    function schedulePoll() {
        if (poll) return;
        poll = setTimeout(() => { poll = null; load(); }, 3000);
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
        try { renderStatus((await api('/api/routes/learning/auto', { kind: box.dataset.kind, auto: box.checked })).status); }
        catch (e) { showError(e.message); box.checked = !box.checked; box.disabled = false; }
    }

    function init() {
        const y = new Date(); y.setDate(y.getDate() - 1);
        const from = new Date(y); from.setDate(from.getDate() - 13);
        $('lrTo').value = isoDay(y);
        $('lrFrom').value = isoDay(from);
        $('lrPeriod').addEventListener('submit', (ev) => { ev.preventDefault(); load(); });
        $('lrRun').addEventListener('click', run);
        $('lrLearnRows').addEventListener('change', toggle);
        load();
    }
    document.addEventListener('DOMContentLoaded', init);
})();

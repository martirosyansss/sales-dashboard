/* «Пульт производства» /production — очередь внимания → план партий → данные.
   Промышленный ops-console: спарклайны, шкалы покрытия, табличные цифры.
   Data: /api/production/*. План: docs/plans/warehouse-production-plan.md. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const state = {
        data: null, whatIf: 0, sortKey: 'cover_days', sortAsc: true,
        statusFilter: null, showAll: false, simple: true, queueExpanded: false,
        flowChart: null, timelineChart: null, pmChart: null,
    };

    const PAL = { acc: '#34d3c4', danger: '#ff5a6a', warn: '#ffb03a', ok: '#3fd08a',
                  dim: '#a7b0c0', line: 'rgba(255,255,255,.07)' };
    // Уважаем системную настройку «уменьшить движение» (WCAG / Apple HIG)
    const RM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const chartAnim = RM ? false : { duration: 350, easing: 'easeOutQuart' };

    // Сделать элемент активируемым с клавиатуры (Enter/Space) как кнопку
    function keyActivatable(el, onActivate, label) {
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        if (label) el.setAttribute('aria-label', label);
        el.addEventListener('click', onActivate);
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onActivate(); }
        });
    }

    const fmt = (v, d = 0) => (v === null || v === undefined) ? '—'
        : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: d });
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    // «Дни покрытия» -> человеческий срок
    function humanDays(d) {
        if (d === null || d === undefined) return '—';
        if (d <= 0) return 'закончился';
        if (d < 1) return 'меньше дня';
        if (d < 1.5) return '1 день';
        if (d < 7) return Math.round(d) + ' дн.';
        if (d < 28) return '≈' + Math.round(d / 7) + ' нед.';
        return '≈' + Math.round(d / 30) + ' мес.';
    }
    // Русское склонение
    function plural(n, one, few, many) {
        const a = Math.abs(n) % 100, b = a % 10;
        if (a > 10 && a < 20) return many;
        if (b > 1 && b < 5) return few;
        if (b === 1) return one;
        return many;
    }
    // Вердикт «что делать» простыми словами
    const VERDICT = {
        critical: ['critical', 'Срочно делать'], overstock: ['overstock', 'Избыток'],
        ok: ['ok', 'В норме'], no_forecast: ['mute', 'Нет данных'], no_data: ['mute', '—'],
    };
    function verdictHTML(status) {
        const [cls, txt] = VERDICT[status] || ['mute', '—'];
        return `<span class="pc-verdict ${cls}">${txt}</span>`;
    }
    // Когда начинать — из запаса (дней покрытия) в понятный срок
    function whenVerdict(d) {
        if (d === null || d === undefined) return { cls: 'mute', txt: '—' };
        if (d < 1) return { cls: 'critical', txt: 'Сегодня' };
        if (d < 3) return { cls: 'critical', txt: 'Срочно' };
        if (d < 7) return { cls: 'overstock', txt: 'Эта неделя' };
        if (d < 21) return { cls: 'ok', txt: 'След. неделя' };
        return { cls: 'mute', txt: 'Можно позже' };
    }
    function whenHTML(d) { const w = whenVerdict(d); return `<span class="pc-verdict ${w.cls}">${w.txt}</span>`; }
    // Тег «когда» для строки: день из плана (если расписание есть), иначе срочность
    function whenTag(r) {
        const b = ((state.data && state.data.plan && state.data.plan.batches) || []).find(x => x.pid === r.pid);
        if (b && b.day) return { cls: 'ok', txt: 'День ' + b.day };
        return whenVerdict(r.cover_days);
    }

    const LS_KEY = 'prodFilters';
    function saveFilters() {
        try { localStorage.setItem(LS_KEY, JSON.stringify({
            storage: $('prodStorageFilter').value, group: $('prodGroupFilter').value })); } catch (e) {}
    }
    function loadFilters() { try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch (e) { return {}; } }

    // ---------- Микро-визуализации ----------

    // Спарклайн из массива значений -> inline SVG (наследует currentColor для статуса)
    function sparkSVG(arr, w = 78, h = 22) {
        const vals = (arr && arr.length) ? arr : [0];
        const max = Math.max(...vals, 1), min = Math.min(...vals, 0);
        const rng = (max - min) || 1;
        const n = vals.length;
        const x = i => (n === 1 ? w / 2 : (i / (n - 1)) * (w - 2) + 1);
        const y = v => h - 2 - ((v - min) / rng) * (h - 4);
        let d = '';
        vals.forEach((v, i) => { d += (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
        const area = d + `L${x(n - 1).toFixed(1)} ${h} L${x(0).toFixed(1)} ${h} Z`;
        const lx = x(n - 1).toFixed(1), ly = y(vals[n - 1]).toFixed(1);
        return `<svg class="pc-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
            `<path class="area" d="${area}"/><path class="line" d="${d}"/>` +
            `<circle cx="${lx}" cy="${ly}" r="1.7"/></svg>`;
    }

    // Шкала покрытия (bullet-chart): маркеры критичного, цели и «затоварено»
    function gaugeHTML(cover, s) {
        const crit = s.settings.critical_cover_days, over = s.settings.overstock_cover_days;
        const tgt = s.settings.target_cover_days;
        const scale = over * 1.25;
        const pct = cover === null ? 0 : Math.min(100, (cover / scale) * 100);
        const cm = (crit / scale) * 100, om = (over / scale) * 100, tm = (tgt / scale) * 100;
        const valTxt = cover === null ? '0' : fmt(cover, 1);
        return `<div class="pc-gauge">` +
            `<div class="pc-gauge-track"><span class="fill" style="width:${pct}%"></span>` +
            `<span class="mark" style="left:${cm}%"></span><span class="mark" style="left:${om}%"></span>` +
            `<span class="target" style="left:${tm}%" title="цель ${tgt} дн"></span></div>` +
            `<span class="pc-gauge-val pc-num">${valTxt} дн</span></div>`;
    }

    // ---------- What-if ----------
    function whatIfQty(r) {
        if (!r.prescriptive || r.forecast === null) return r.production_qty;
        const k = 1 + state.whatIf / 100;
        return Math.round(Math.max(0, r.forecast * k + r.target_end * k - r.stock_start));
    }

    // ---------- Загрузка ----------
    async function loadOverview(refresh) {
        $('prodLoading').classList.remove('d-none');
        $('prodError').classList.add('d-none');
        $('prodBody').classList.add('d-none');
        $('prodRibbon').classList.add('d-none');
        const p = new URLSearchParams();
        const st = $('prodStorageFilter').value, gr = $('prodGroupFilter').value;
        if (st) p.set('storage', st);
        if (gr) p.set('group', gr);
        if (refresh) p.set('refresh', '1');
        try {
            const resp = await fetch('/api/production/overview?' + p);
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                if (data.settings_error) {
                    $('prodSettingsError').querySelector('span').textContent = data.error;
                    $('prodSettingsError').classList.remove('d-none');
                }
                throw new Error(data.error || ('HTTP ' + resp.status));
            }
            state.data = data;
            renderAll();
        } catch (e) {
            $('prodErrorText').textContent = 'Не удалось загрузить данные: ' + e.message;
            $('prodError').classList.remove('d-none');
        } finally { $('prodLoading').classList.add('d-none'); }
    }

    // ---------- Рендер ----------
    function renderAll() {
        const d = state.data;
        state.whatIf = 0; $('whatIfSlider').value = 0; $('whatIfValue').textContent = '0%';
        $('prodBody').classList.remove('d-none');
        $('prodRibbon').classList.remove('d-none');
        $('prodTargetMonth').textContent = 'ПЛАН · ' + d.target_month.slice(0, 7);
        $('planTargetNote').textContent = 'на ' + d.target_month.slice(0, 7) + ' · факт по ' + d.last_full_month;
        fillFilters();
        renderSummary();
        renderRibbon();
        renderQueue();
        renderPlan(d.plan);
        renderFlow();
        renderTable();
    }

    // Сводка дня простыми словами
    function renderSummary() {
        const d = state.data, k = d.kpis;
        const crit = (d.risk_board || []).filter(r => r.status === 'critical');
        let msg;
        if (!crit.length) {
            msg = 'Сейчас всё в порядке — ничего срочно производить не нужно.';
        } else {
            const top = crit[0];
            const daily = top.forecast ? Math.max(1, Math.round(top.forecast / 30)) : null;
            msg = `Скоро закончатся <b class="danger">${k.deficit_count}</b> ${plural(k.deficit_count, 'товар', 'товара', 'товаров')}. ` +
                `Срочнее всего <b>${esc(top.name)}</b> — осталось <b class="danger">${humanDays(top.cover_days)}</b>` +
                (daily ? `, продаём ~${fmt(daily)} ${esc(top.unit || 'шт')}/день` : '') +
                `. Нужно сделать <b>${fmt(whatIfQty(top))} ${esc(top.unit || 'шт')}</b>.`;
        }
        if (k.overstock_count) {
            msg += ` Ещё <b class="warn">${k.overstock_count}</b> ${plural(k.overstock_count, 'товар лежит', 'товара лежат', 'товаров лежат')} с избытком.`;
        }
        $('prodSummary').innerHTML = '<i class="fas fa-lightbulb" aria-hidden="true"></i><span>' + msg + '</span>';
    }

    function fillFilters() {
        const d = state.data, stSel = $('prodStorageFilter'), grSel = $('prodGroupFilter'), saved = loadFilters();
        if (stSel.options.length <= 1) {
            (d.storages || []).forEach(s => { const o = document.createElement('option');
                o.value = s.code; o.textContent = s.code + ' — ' + s.name; stSel.appendChild(o); });
            if (saved.storage) stSel.value = saved.storage;
        }
        if (grSel.options.length <= 1) {
            const g = new Map(); d.rows.forEach(r => { if (r.group) g.set(r.group, r.group_name); });
            [...g.entries()].sort((a, b) => a[1].localeCompare(b[1], 'ru')).forEach(([c, n]) => {
                const o = document.createElement('option'); o.value = c; o.textContent = n; grSel.appendChild(o); });
            if (saved.group) grSel.value = saved.group;
        }
    }

    function renderRibbon() {
        const k = state.data.kpis;
        $('cntDeficit').textContent = fmt(k.deficit_count);
        $('cntOverstock').textContent = fmt(k.overstock_count);
        $('cntActive').textContent = fmt(k.active_skus);
        const wape = k.wape || {}, w = (wape.AB ?? wape.A);
        const box = $('cntWapeBox');
        if (w === null || w === undefined) { $('cntWape').textContent = '—'; $('cntWapeSub').textContent = 'мало истории'; box.title = ''; }
        else {
            const acc = Math.max(0, 100 - Math.round(w));   // точность = 100 − ошибка
            $('cntWape').textContent = '~' + acc + '%';
            $('cntWape').style.color = w < 20 ? PAL.ok : (w < 35 ? PAL.warn : PAL.danger);
            $('cntWapeSub').textContent = w < 20 ? 'прогноз обычно сбывается' : (w < 35 ? 'бывают ошибки — следите' : 'часто ошибается, проверяйте');
            box.title = 'В среднем прогноз по ходовым товарам попадает на ~' + acc + '%. ' +
                'Остальное корректируйте слайдером «Спрос» и своим опытом.';
        }
        document.querySelectorAll('.pc-tile[data-filter]').forEach(el => {
            const on = el.dataset.filter === state.statusFilter;
            el.classList.toggle('pc-active', on);
            el.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    function narrative(r) {
        const qty = whatIfQty(r), k = 1 + state.whatIf / 100;
        const left = (r.stock_start != null && r.forecast != null)
            ? Math.round(r.stock_start + qty - r.forecast * k) : null;
        const days = r.forecast > 0 ? Math.round((left / (r.forecast / 30)) || 0) : null;
        return 'Сейчас <b>' + fmt(r.stock_wh) + '</b> → спрос <b>' + fmt(r.forecast) +
            '</b> → произвести <b>' + fmt(qty) + '</b> → останется <b>' + fmt(left) + '</b>' +
            (days != null ? ' (~' + days + ' дн)' : '');
    }

    function renderQueue() {
        const box = $('riskBoard'); box.innerHTML = '';
        const all = state.data.risk_board || [];
        $('riskEmpty').classList.toggle('d-none', all.length > 0);
        const CAP = 6;
        const items = state.queueExpanded ? all : all.slice(0, CAP);
        items.forEach(r => {
            const el = document.createElement('div');
            el.className = 'pc-qrow';
            const sev = r.status === 'critical' ? 'critical' : 'warn';
            const col = r.status === 'critical' ? PAL.danger : PAL.warn;
            el.innerHTML =
                `<span class="pc-qbar s-${sev}"></span>` +
                `<div class="pc-qname"><div class="n">${esc(r.name)}</div>` +
                    `<div class="g">делать: <span class="pc-when ${whenTag(r).cls}">${esc(whenTag(r).txt)}</span></div></div>` +
                `<div class="pc-qm"><span class="v">${fmt(r.stock_wh)}</span><span class="k">на складе</span></div>` +
                `<div class="pc-qm"><span class="v">${fmt(r.sales_month)}</span><span class="k">расход/мес</span></div>` +
                `<div class="pc-qm m-danger"><span class="v">${humanDays(r.cover_days)}</span><span class="k">осталось</span></div>` +
                `<div class="pc-qm acc"><span class="v">${fmt(whatIfQty(r))}</span><span class="k">сделать</span></div>`;
            keyActivatable(el, () => openModal(r),
                `${r.name}: на складе ${fmt(r.stock_wh)}, расход ${fmt(r.sales_month)} в месяц, осталось ${humanDays(r.cover_days)}, сделать ${fmt(whatIfQty(r))}. Открыть карточку`);
            box.appendChild(el);
        });
        if (all.length > CAP) {
            const more = document.createElement('button');
            more.className = 'pc-queue-more'; more.type = 'button';
            more.textContent = state.queueExpanded ? 'Свернуть' : `Показать все ${all.length}`;
            more.addEventListener('click', () => { state.queueExpanded = !state.queueExpanded; renderQueue(); });
            box.appendChild(more);
        }
    }

    // ---------- План ----------
    function batchQty(b) { const r = state.data.rows.find(x => x.pid === b.pid); return r ? (whatIfQty(r) ?? b.qty) : b.qty; }
    function batchHours(b) { return b.rate ? batchQty(b) / b.rate : null; }

    function renderPlan(plan) {
        $('planAssumptions').innerHTML =
            '<b style="color:var(--pc-fg)">Делайте по порядку — сверху самое срочное.</b> ' + esc(plan.assumptions || '');
        const onb = $('planOnboarding'); onb.classList.add('d-none');
        if (!plan.has_schedule && plan.batches.length) {
            let msg = '<i class="fas fa-circle-info me-2"></i>';
            if (plan.no_line && plan.no_line.length) {
                msg += 'Нет линии для: <b>' + plan.no_line.slice(0, 5).map(esc).join(', ') +
                    (plan.no_line.length > 5 ? ' и ещё ' + (plan.no_line.length - 5) : '') + '</b>. ';
            }
            if (plan.missing_rate_groups && plan.missing_rate_groups.length) {
                msg += 'Не задана скорость у линий: <b>' + plan.missing_rate_groups.map(esc).join(', ') + '</b>. ';
            }
            msg += 'Настройте в <a href="/production/settings">Настройки → Линии</a>, чтобы построить график по дням.';
            onb.innerHTML = msg;
            onb.classList.remove('d-none');
        }
        const empty = $('planEmpty'); empty.classList.add('d-none');
        if (!plan.batches.length) {
            empty.innerHTML = '<i class="fas fa-circle-check ok me-2"></i>Производить нечего: все A/B-позиции покрыты.';
            empty.classList.remove('d-none');
        }
        renderTimeline(plan);
        renderPlanTable(plan);
    }

    function renderTimeline(plan) {
        const wrap = $('planTimelineWrap');
        const lines = plan.lines || [];
        const scheduledLines = lines.filter(l => l.used_hours != null);
        if (!scheduledLines.length || !plan.batches.length) {
            wrap.classList.add('d-none');
            if (state.timelineChart) { state.timelineChart.destroy(); state.timelineChart = null; }
            renderCapacity(plan);
            return;
        }
        wrap.classList.remove('d-none');
        // Строка на линию (y-ось). Каждая партия — сегмент на строке своей линии (x = часы).
        const lineNames = scheduledLines.map(l => l.name);
        const idxOf = {}; scheduledLines.forEach((l, i) => { idxOf[l.id] = i; });
        const famColors = {}; const palette = ['#34d3c4', '#3fd08a', '#ffb03a', '#8db4ff', '#c88dff', '#59d2e6', '#d3e05e'];
        let ci = 0; const datasets = [];
        const zero = () => new Array(lineNames.length).fill(0);
        plan.batches.forEach(b => {
            if (idxOf[b.line_id] === undefined || b.hours == null) return;
            const i = idxOf[b.line_id];
            if (b.changeover_h > 0) {
                const d = zero(); d[i] = b.changeover_h;
                datasets.push({ data: d, backgroundColor: hatch(), stack: 's', meta: { t: 'ch', h: b.changeover_h } });
            }
            if (!(b.family in famColors)) famColors[b.family] = palette[ci++ % palette.length];
            const d = zero(); d[i] = b.hours;
            datasets.push({ data: d, backgroundColor: b.fits ? famColors[b.family] : 'rgba(255,90,106,.85)', stack: 's', meta: { t: 'b', b } });
        });
        // высота под число линий
        $('planTimeline').style.height = Math.max(70, lineNames.length * 42) + 'px';
        if (state.timelineChart) state.timelineChart.destroy();
        state.timelineChart = new Chart($('planTimeline').getContext('2d'), {
            type: 'bar', data: { labels: lineNames, datasets },
            options: {
                indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: chartAnim,
                plugins: { legend: { display: false }, datalabels: { display: false },
                    tooltip: { callbacks: { label: c => { const m = c.dataset.meta;
                        if (m.t === 'ch') return 'Переналадка: ' + m.h.toFixed(1) + ' ч';
                        return m.b.name + ': ' + fmt(batchQty(m.b)) + ' ' + (m.b.unit || 'шт') + ' · день ' + (m.b.day || '?') + ' · ' + m.b.hours.toFixed(1) + ' ч'; } } } },
                scales: { x: { stacked: true, grid: { color: PAL.line }, ticks: { color: PAL.dim, font: { family: 'IBM Plex Mono', size: 10 } }, title: { display: true, text: 'часы работы линии', color: PAL.dim } },
                          y: { stacked: true, ticks: { color: '#eef1f6', font: { family: 'Archivo', size: 11 } }, grid: { display: false } } },
            },
        });
        renderCapacity(plan);
    }

    // Сводка мощности по линиям
    function renderCapacity(plan) {
        const el = $('planCapacity');
        const lines = (plan.lines || []).filter(l => l.batches);
        if (!lines.length) { el.innerHTML = ''; return; }
        const parts = lines.map(l => {
            const u = l.utilization;
            const load = u == null ? '' : ` · загрузка <b class="${u > 100 ? 'over' : ''}">${u}%</b>`;
            const nf = l.not_fits ? ` · <span class="over">не помещается ${l.not_fits}</span>` : '';
            const days = l.max_day ? ` · ${l.max_day} дн` : '';
            return `<div class="pc-linerow"><b style="color:var(--pc-fg)">${esc(l.name)}</b>: ${l.batches} партий${days}${load}${nf}</div>`;
        });
        const nl = (plan.no_line && plan.no_line.length)
            ? `<div class="pc-linerow" style="color:var(--pc-danger)">⚠ Нет линии для: ${plan.no_line.slice(0, 6).map(esc).join(', ')}${plan.no_line.length > 6 ? ' и ещё ' + (plan.no_line.length - 6) : ''} — назначьте в Настройки → Линии</div>`
            : '';
        el.innerHTML = parts.join('') + nl;
    }

    let hatchCanvas = null;
    function hatch() {
        if (!hatchCanvas) { hatchCanvas = document.createElement('canvas'); hatchCanvas.width = hatchCanvas.height = 8;
            const c = hatchCanvas.getContext('2d'); c.fillStyle = 'rgba(139,147,167,.22)'; c.fillRect(0, 0, 8, 8);
            c.strokeStyle = 'rgba(139,147,167,.7)'; c.beginPath(); c.moveTo(0, 8); c.lineTo(8, 0); c.stroke(); }
        return $('planTimeline').getContext('2d').createPattern(hatchCanvas, 'repeat');
    }

    function statusPill(s) {
        const map = { critical: ['critical', 'критично'], overstock: ['overstock', 'затоварено'],
                      ok: ['ok', 'ок'], no_forecast: ['mute', 'нет прогноза'], no_data: ['mute', '—'] };
        const [cls, txt] = map[s] || ['mute', '—'];
        return `<span class="pc-pill ${cls}">${txt}</span>`;
    }

    function renderPlanTable(plan) {
        const tb = $('planTable').querySelector('tbody'); tb.innerHTML = '';
        plan.batches.forEach(b => {
            const tr = document.createElement('tr'); const h = batchHours(b);
            const noLine = !b.line_id;
            tr.innerHTML = `<td class="l pc-cell-dim">${b.order}</td><td class="l">${esc(b.name)}</td>` +
                `<td class="l" style="${noLine ? 'color:var(--pc-danger)' : ''}">${esc(b.line_name || '—')}</td>` +
                `<td title="${b.cover_days === null ? '' : 'точно: ' + fmt(b.cover_days, 1) + ' дн.'}">${humanDays(b.cover_days)}</td>` +
                `<td style="color:var(--pc-acc);font-weight:600">${fmt(batchQty(b))}</td>` +
                `<td class="l pc-cell-dim pc-col-exp">${esc(b.family)}</td>` +
                `<td class="pc-col-exp">${h === null ? '—' : h.toFixed(1)}</td>` +
                `<td class="pc-cell-dim pc-col-exp">${b.changeover_h ? b.changeover_h.toFixed(1) : '—'}</td>` +
                `<td class="l">${noLine ? '<span class="pc-verdict critical">нет линии</span>' : (!b.fits ? '<span class="pc-verdict critical">Не помещается</span>' : (b.day ? '<span class="pc-verdict ok">День ' + b.day + '</span>' : whenHTML(b.cover_days)))}</td>`;
            keyActivatable(tr, () => { const r = state.data.rows.find(x => x.pid === b.pid); if (r) openModal(r); },
                `Партия ${b.order}: ${b.name}, произвести ${fmt(batchQty(b))}. Открыть карточку`);
            tb.appendChild(tr);
        });
    }

    function renderFlow() {
        const ch = state.data.chart; if (!ch) return;
        if (state.flowChart) state.flowChart.destroy();
        state.flowChart = new Chart($('flowChart').getContext('2d'), {
            type: 'bar', data: { labels: ch.months, datasets: [
                { label: 'Приход', data: ch.receipts, backgroundColor: 'rgba(52,211,196,.65)', borderRadius: 3 },
                { label: 'Спрос (нетто)', data: ch.sales, backgroundColor: 'rgba(141,180,255,.6)', borderRadius: 3 } ] },
            options: { responsive: true, maintainAspectRatio: false, animation: chartAnim,
                plugins: { legend: { labels: { color: PAL.dim, font: { family: 'Archivo' } } }, datalabels: { display: false } },
                scales: { x: { grid: { display: false }, ticks: { color: PAL.dim, font: { family: 'IBM Plex Mono', size: 10 } } },
                          y: { grid: { color: PAL.line }, ticks: { color: PAL.dim, font: { family: 'IBM Plex Mono', size: 10 } } } } },
        });
        // Текстовое описание для скринридеров (screen-reader summary)
        const tot_r = ch.receipts.reduce((a, b) => a + b, 0), tot_s = ch.sales.reduce((a, b) => a + b, 0);
        $('flowChart').setAttribute('aria-label',
            `Приход и спрос за 12 месяцев ${ch.months[0]}–${ch.months[ch.months.length - 1]}. ` +
            `Суммарный приход ${fmt(tot_r)}, суммарный спрос ${fmt(tot_s)} штук.`);
    }

    // ---------- Таблица ----------
    function clsBadge(r) {
        if (r.out_of_contour) return '<span class="pc-cls oc" title="Движение только через исключённые склады">вне склада</span>';
        if (r.cls === 'A') return '<span class="pc-cls A">A</span>';
        if (r.cls === 'B') return '<span class="pc-cls B">B</span>';
        return '<span class="pc-cls C" title="Класс C: недостаточно статистики — вручную">C</span>';
    }
    function covCell(r) {
        if (r.cover_days === null) return '<span class="pc-cell-dim">—</span>';
        const over = state.data.settings.overstock_cover_days;
        const scale = over * 1.25, pct = Math.min(100, (r.cover_days / scale) * 100);
        const col = r.status === 'critical' ? PAL.danger : (r.status === 'overstock' ? PAL.warn : PAL.ok);
        return `<span class="pc-covcell" title="точно: ${fmt(r.cover_days, 1)} дн."><span class="pc-covbar"><span style="width:${pct}%;background:${col}"></span></span>` +
            `<span>${humanDays(r.cover_days)}</span></span>`;
    }
    function fcTip(r) {
        if (!r.forecast_parts) return 'Недостаточно истории (нужно ≥2 мес.)';
        const p = r.forecast_parts;
        return 'база ' + p.base + ' × сезонность ' + p.coeff + ' × тренд ' + p.trend +
            (p.capped ? ' · ограничен потолком (1.5× лучшего месяца)' : '') +
            (p.deficit_months ? ' · дефицитных мес. исключено: ' + p.deficit_months : '');
    }

    function visibleRows() {
        const q = ($('prodSearch').value || '').trim().toLowerCase();
        let rows = state.data.rows.filter(r => r.avg12 > 0 || r.stock_wh > 0 || r.stock_agent > 0);
        if (!state.showAll) rows = rows.filter(r => r.cls !== 'C' || r.status === 'overstock');
        if (state.statusFilter) rows = rows.filter(r => r.status === state.statusFilter);
        if (q) rows = rows.filter(r => r.name.toLowerCase().includes(q) || (r.code || '').toLowerCase().includes(q));
        const key = state.sortKey, asc = state.sortAsc ? 1 : -1;
        rows.sort((a, b) => {
            let va = a[key], vb = b[key];
            if (key === 'production_qty') { va = whatIfQty(a); vb = whatIfQty(b); }
            if (va == null) va = state.sortAsc ? Infinity : -Infinity;
            if (vb == null) vb = state.sortAsc ? Infinity : -Infinity;
            if (typeof va === 'string') return va.localeCompare(vb, 'ru') * asc;
            return (va - vb) * asc;
        });
        return rows;
    }

    function renderTable() {
        const rows = visibleRows(), tb = $('mainTable').querySelector('tbody'); tb.innerHTML = '';
        $('tableEmpty').classList.toggle('d-none', rows.length > 0);
        $('tableInfo').textContent = 'ПОКАЗАНО ' + rows.length + ' / ' + state.data.rows.length +
            (state.statusFilter ? '  ·  фильтр: ' + (state.statusFilter === 'critical' ? 'дефицит' : 'затоварено') + ' ✕' : '');
        const frag = document.createDocumentFragment();
        rows.forEach(r => {
            const tr = document.createElement('tr'); const qty = whatIfQty(r);
            const sparkCol = r.status === 'critical' ? PAL.danger : (r.status === 'overstock' ? PAL.warn : PAL.dim);
            tr.innerHTML =
                `<td class="l pc-sticky-col"><div class="pc-cell-name"><span class="n">${esc(r.name)}</span><span class="c">${esc(r.code || '')}</span></div></td>` +
                `<td>${fmt(r.stock_wh)}</td>` +
                `<td>${fmt(r.sales_month)}</td>` +
                `<td class="pc-key" style="${qty ? 'color:var(--pc-acc);font-weight:700' : 'color:var(--pc-fg-mute)'}">${qty == null ? '—' : fmt(qty)}</td>` +
                `<td>${covCell(r)}</td>` +
                `<td class="l">${verdictHTML(r.status)}</td>` +
                `<td class="pc-cell-dim pc-col-exp">${fmt(r.inp_month)}</td>` +
                `<td class="pc-cell-dim pc-col-exp">${fmt(r.stock_agent)}</td>` +
                `<td class="pc-cell-dim pc-col-exp">${fmt(r.avg12, 1)}</td>` +
                `<td class="pc-col-exp" title="${fcTip(r)}">${r.forecast === null ? '—' : fmt(r.forecast)}</td>` +
                `<td class="l pc-spark-cell pc-col-exp" style="color:${sparkCol}">${sparkSVG(r.spark || [])}</td>` +
                `<td class="l pc-col-exp">${clsBadge(r)}</td>`;
            tr.addEventListener('click', () => openModal(r));
            frag.appendChild(tr);
        });
        tb.appendChild(frag);
    }

    // ---------- Модалка ----------
    async function openModal(r) {
        $('pmTitle').textContent = r.name + (r.code ? ' · ' + r.code : '');
        // Три главных числа крупно: склад / расход / надо сделать
        const u = esc(r.unit || 'шт'), qty = whatIfQty(r);
        $('pmStats').innerHTML =
            `<div class="pc-mstat"><span class="v pc-num">${fmt(r.stock_wh)}</span><span class="u">${u}</span><span class="k">на складе</span></div>` +
            `<div class="pc-mstat"><span class="v pc-num">${fmt(r.sales_month)}</span><span class="u">${u}/мес</span><span class="k">расход</span></div>` +
            `<div class="pc-mstat acc"><span class="v pc-num">${qty == null ? '—' : fmt(qty)}</span><span class="u">${qty == null ? '' : u}</span><span class="k">надо сделать</span></div>`;
        $('pmNarrative').innerHTML = r.forecast != null ? narrative(r)
            : 'Недостаточно истории для прогноза.';
        $('pmError').classList.add('d-none'); $('pmLoading').classList.remove('d-none');
        if (state.pmChart) { state.pmChart.destroy(); state.pmChart = null; }
        bootstrap.Modal.getOrCreateInstance($('productModal')).show();
        try {
            const resp = await fetch('/api/production/product/' + r.pid + '/history');
            const data = await resp.json();
            if (!data.success) throw new Error(data.error || 'ошибка');
            state.pmChart = new Chart($('pmChart').getContext('2d'), {
                type: 'bar', data: { labels: data.months, datasets: [
                    { label: 'Приход', data: data.receipts, backgroundColor: 'rgba(52,211,196,.65)', borderRadius: 3 },
                    { label: 'Продажи', data: data.sales, backgroundColor: 'rgba(141,180,255,.6)', borderRadius: 3 },
                    { label: 'Возвраты', data: data.returns, backgroundColor: 'rgba(255,176,58,.6)', borderRadius: 3 } ] },
                options: { responsive: true, maintainAspectRatio: false, animation: chartAnim,
                    plugins: { legend: { labels: { color: PAL.dim, font: { family: 'Archivo' } } }, datalabels: { display: false } },
                    scales: { x: { grid: { display: false }, ticks: { color: PAL.dim, font: { family: 'IBM Plex Mono', size: 10 } } },
                              y: { grid: { color: PAL.line }, ticks: { color: PAL.dim, font: { family: 'IBM Plex Mono', size: 10 } } } } },
            });
            $('pmChart').setAttribute('aria-label', 'История движений товара за 12 месяцев: приход, продажи, возвраты');
        } catch (e) { $('pmError').textContent = 'Нет истории движений: ' + e.message; $('pmError').classList.remove('d-none'); }
        finally { $('pmLoading').classList.add('d-none'); }
    }

    // ---------- Пересчёт ----------
    async function recalc() {
        const btn = $('recalcBtn'); btn.disabled = true;
        const html = btn.innerHTML; btn.innerHTML = '<span class="pc-spinner" style="width:15px;height:15px;margin:0;border-width:2px"></span>Пересчёт';
        try {
            const resp = await fetch('/api/production/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ storage: $('prodStorageFilter').value, group: $('prodGroupFilter').value }) });
            const data = await resp.json(); if (!data.success) throw new Error(data.error || 'ошибка');
            state.data.rows = data.rows; state.data.plan = data.plan; state.data.kpis.wape = data.wape;
            renderAll();
        } catch (e) { alert('Не удалось пересчитать: ' + e.message); }
        finally { btn.disabled = false; btn.innerHTML = html; }
    }

    // ---------- AI ----------
    async function runAi() {
        const btn = $('aiBtn'), out = $('aiOutput'); btn.disabled = true;
        out.classList.remove('d-none'); out.textContent = 'Готовлю пояснение…';
        let text = '';
        try {
            const resp = await fetch('/api/production/ai-plan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ storage: $('prodStorageFilter').value, group: $('prodGroupFilter').value }) });
            if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || ('HTTP ' + resp.status)); }
            const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = '';
            for (;;) { const { done, value } = await reader.read(); if (done) break;
                buf += dec.decode(value, { stream: true }); const parts = buf.split('\n\n'); buf = parts.pop();
                for (const pp of parts) { if (!pp.startsWith('data: ')) continue; const m = JSON.parse(pp.slice(6));
                    if (m.t) { text += m.t; out.innerHTML = md(text); } if (m.error) throw new Error(m.error); } }
        } catch (e) { out.innerHTML = '<span class="text-danger">AI недоступен: ' + esc(e.message) + '</span>'; }
        finally { btn.disabled = false; }
    }
    function md(src) {
        return esc(src).split('\n').map(l => {
            if (/^#{1,3} /.test(l)) return '<h3>' + l.replace(/^#{1,3} /, '') + '</h3>';
            if (/^\d+\. /.test(l) || /^[-*] /.test(l)) return '<div>• ' + l.replace(/^(\d+\. |[-*] )/, '') + '</div>';
            return l ? '<div>' + l + '</div>' : '';
        }).join('').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    }

    // ---------- Excel ----------
    function exportExcel() {
        const plan = state.data.plan;
        const wsData = [['#', 'Товар', 'Семейство', 'Покрытие, дн', 'Произвести', 'Ед.', 'Часы', 'Переналадка, ч', 'Помещается']];
        plan.batches.forEach(b => wsData.push([b.order, b.name, b.family, b.cover_days, batchQty(b), b.unit, batchHours(b), b.changeover_h, b.fits ? 'да' : 'НЕТ']));
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(wsData), 'План');
        const rows = visibleRows().map(r => ({ 'Товар': r.name, 'Класс': r.cls, 'Статус': r.status,
            'Покрытие, дн': r.cover_days, 'Произвести': whatIfQty(r), 'Остаток': r.stock_wh, 'У агентов': r.stock_agent,
            'Приход/мес': r.inp_month, 'Спрос/мес': r.sales_month, 'Прогноз': r.forecast }));
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), 'Товары');
        XLSX.writeFile(wb, 'production_' + state.data.target_month.slice(0, 7) + '.xlsx');
    }

    // ---------- События ----------
    document.addEventListener('DOMContentLoaded', () => {
        loadOverview(false);
        $('prodRefreshBtn').addEventListener('click', () => loadOverview(true));
        $('prodRetryBtn').addEventListener('click', () => loadOverview(false));
        $('prodStorageFilter').addEventListener('change', () => { saveFilters(); loadOverview(false); });
        $('prodGroupFilter').addEventListener('change', () => { saveFilters(); loadOverview(false); });
        $('prodSearch').addEventListener('input', renderTable);
        $('showAllToggle').addEventListener('change', e => { state.showAll = e.target.checked; renderTable(); });
        $('tableResetBtn').addEventListener('click', () => { $('prodSearch').value = ''; $('prodGroupFilter').value = '';
            state.statusFilter = null; saveFilters(); loadOverview(false); });
        $('recalcBtn').addEventListener('click', recalc);
        $('aiBtn').addEventListener('click', runAi);
        $('exportBtn').addEventListener('click', exportExcel);

        // Переключатель Просто / Подробно
        function setSimple(on) {
            state.simple = on;
            $('mainTable').classList.toggle('simple', on);
            $('planTable').classList.toggle('simple', on);
            $('segSimple').classList.toggle('active', on);
            $('segDetail').classList.toggle('active', !on);
            try { localStorage.setItem('prodSimple', on ? '1' : '0'); } catch (e) {}
        }
        $('segSimple').addEventListener('click', () => setSimple(true));
        $('segDetail').addEventListener('click', () => setSimple(false));
        try { if (localStorage.getItem('prodSimple') === '0') setSimple(false); } catch (e) {}
        document.querySelectorAll('.pc-tile[data-filter]').forEach(el => el.addEventListener('click', () => {
            const f = el.dataset.filter; state.statusFilter = state.statusFilter === f ? null : f;
            if (state.statusFilter === 'overstock') { state.showAll = true; $('showAllToggle').checked = true; }
            renderRibbon(); renderTable(); $('mainTable').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }));
        // Заголовки таблиц: scope для скринридеров
        document.querySelectorAll('#mainTable thead th, #planTable thead th').forEach(th => th.setAttribute('scope', 'col'));
        // Сортируемые заголовки: клавиатура + aria-sort
        function doSort(th) {
            const k = th.dataset.sort;
            if (state.sortKey === k) state.sortAsc = !state.sortAsc; else { state.sortKey = k; state.sortAsc = true; }
            document.querySelectorAll('#mainTable .arr').forEach(a => a.remove());
            document.querySelectorAll('#mainTable .sortable').forEach(h => h.setAttribute('aria-sort', 'none'));
            th.setAttribute('aria-sort', state.sortAsc ? 'ascending' : 'descending');
            const arr = document.createElement('span'); arr.className = 'arr';
            arr.textContent = state.sortAsc ? ' ▲' : ' ▼'; th.appendChild(arr);
            renderTable();
        }
        document.querySelectorAll('#mainTable .sortable').forEach(th => {
            th.setAttribute('tabindex', '0'); th.setAttribute('aria-sort', 'none');
            th.addEventListener('click', () => doSort(th));
            th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doSort(th); } });
        });
        $('whatIfSlider').addEventListener('input', e => { state.whatIf = parseInt(e.target.value, 10) || 0;
            $('whatIfValue').textContent = (state.whatIf > 0 ? '+' : '') + state.whatIf + '%';
            renderQueue(); renderPlanTable(state.data.plan); renderTimeline(state.data.plan); renderTable();
        });
    });
})();

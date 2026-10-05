/* «Մեքենաները առցանց» /routes/live — машины на карте сейчас (ответ владельца №76, docs/plans/live-map-plan.md).
   API: GET /api/routes/live?date= (все машины, карточки без трека) и GET /api/routes/live/truck?car=&date= (выбранная:
   путь за день, магазины со статусами, журнал тревог). Расчёты — на сервере (route_optimizer/live.py); здесь только показ.
   Опрос — раз в 15 с и только пока вкладка видна. Всё, что пришло с сервера, выводится только через textContent. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const POLL_MS = 15000;
    const YEREVAN = [40.1792, 44.4991];
    const num = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v));
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const hm = (iso) => (typeof iso === 'string' && iso.length >= 16 ? iso.slice(11, 16) : '—');
    const hms = (iso) => (typeof iso === 'string' && iso.length >= 19 ? iso.slice(11, 19) : '—');
    const STATE = {
        moving: ['ընթացքի մեջ', 'is-moving'], standing: ['կանգնած', 'is-standing'], alert: ['ահազանգ', 'is-alert'],
        offline: ['կապ չկա', 'is-offline'], nodata: ['տվյալ չկա', 'is-nodata'], closed: ['օրն ավարտված է', 'is-closed'],
    };
    const ALERT = {
        speed: ['fa-gauge-high', 'Արագության գերազանցում'], stop: ['fa-square-parking', 'Երկար կանգառ ոչ խանութում'],
        no_contact: ['fa-tower-broadcast', 'Կապ չկա'], gps: ['fa-location-crosshairs', 'GPS-ն անջատված է'],
        center: ['fa-ban', 'Փոքր կենտրոնում (մուտքը թույլատրված չէ)'],
    };
    const STORE = { full: ['առաքված', '#45d98f'], covered: ['առաքված', '#45d98f'], partial: ['մասնակի', '#ffb547'],
        refused: ['մերժված', '#ff6b79'], in_progress: ['ընթացքի մեջ', '#38bdf8'], pending: ['դեռ ոչ', '#8693a5'] };
    const NET = { wifi: 'Wi-Fi', cell: 'բջջային', none: 'չկա' };
    const GPS = { on: 'միացված', off: 'անջատված', no_permission: 'թույլտվություն չկա' };

    const state = { date: '', data: null, detail: null, selected: null, timer: null, busy: false, fitted: false,
        map: null, markers: new Map(), layer: null, zone: null };

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c)); });
        return el;
    }
    const icon = (cls) => h('i', { class: 'fas ' + cls, 'aria-hidden': 'true' });
    // подсказка Leaflet со строкой — это HTML: текст сервера (названия магазинов, имена) — только элементом с textContent
    const tip = (text) => h('span', { text });

    function showError(text) { $('lvAlert').hidden = !text; $('lvAlertText').textContent = text || ''; }

    // Ответы дашборда (вход, доступ) — по-русски: свой армянский текст по коду ответа
    async function api(url) {
        let resp;
        try { resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } }); }
        catch (e) { throw new Error('Սերվերը հասանելի չէ։ Ստուգեք կապը։'); }
        if (resp.status === 401) {
            window.location.assign('/login?next=' + encodeURIComponent('/routes/live'));
            throw new Error('Մուտք գործեք նորից։');
        }
        let body = null;
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const text = body && typeof body.error === 'string' && /[Ա-֏]/.test(body.error) ? body.error : null;
            if (resp.status === 403) throw new Error('Մուտքն արգելված է։');
            throw new Error(text || 'Սերվերի սխալ (' + resp.status + ')։ Կրկնեք մի փոքր ուշ։');
        }
        return body;
    }

    const ago = (s) => {
        const n = num(s);
        if (n === null) return '—';
        if (n < 60) return Math.max(0, Math.round(n)) + ' վ առաջ';
        if (n < 3600) return Math.round(n / 60) + ' րոպե առաջ';
        return Math.floor(n / 3600) + ' ժ ' + Math.round((n % 3600) / 60) + ' ր առաջ';
    };
    // «возраст» точки: серый — старше 2 мин, красный — дольше порога «нет связи»
    const ageClass = (s) => {
        const n = num(s), th = state.data ? state.data.thresholds : null;
        if (n === null || !th) return 'is-mute';
        if (n > th.no_contact_min * 60) return 'is-bad';
        return n > th.stale_s ? 'is-mute' : '';
    };
    const delayText = (d) => {
        const n = num(d);
        if (n === null) return null;
        if (Math.abs(n) <= 5) return ['ժամանակին', 'is-ok'];
        return n > 0 ? ['+' + n + ' րոպե ուշացում', n > 15 ? 'is-bad' : 'is-warn'] : [Math.abs(n) + ' րոպե շուտ', 'is-ok'];
    };

    // ---------- список ----------
    function renderSummary(trucks) {
        const count = (st) => trucks.filter(t => t.state === st).length;
        $('lvSummary').replaceChildren(
            ...[['moving', 'ընթացքում'], ['standing', 'կանգնած'], ['alert', 'ահազանգ'], ['offline', 'կապ չկա']]
                .map(([st, label]) => h('div', { class: 'lv-sum is-' + st }, h('b', { text: String(count(st)) }), h('span', { text: label }))));
    }

    function renderList(trucks) {
        $('lvEmpty').hidden = trucks.length > 0;
        const list = $('lvList');
        list.replaceChildren(...trucks.map(t => {
            const [label, cls] = STATE[t.state] || STATE.standing;
            const pos = t.position;
            const meta = [h('span', { text: 'Խանութներ՝ ' + t.stores.done + '/' + t.stores.total })];
            if (pos) meta.push(h('span', { class: ageClass(pos.age_s), text: ago(pos.age_s) }));
            if (t.next && t.next.delay_min !== null) {
                const d = delayText(t.next.delay_min);
                if (d) meta.push(h('span', { class: d[1] === 'is-ok' ? '' : (d[1] === 'is-bad' ? 'is-bad' : 'is-warn'), text: d[0] }));
            }
            const item = h('li', { class: 'lv-item', role: 'option', tabindex: '0', 'aria-selected': String(t.car_code === state.selected),
                'data-car': t.car_code },
                h('span', { class: 'lv-dot ' + cls, title: label }),
                h('div', { class: 'lv-item-main' }, h('div', { class: 'lv-item-plate', text: t.car_code }),
                    h('div', { class: 'lv-item-name', text: [t.name, t.driver].filter(Boolean).join(' · ') || label })),
                h('div', { class: 'lv-item-right' },
                    // скорость — только у свежей точки: у давней (нет связи) она уже не «сейчас»
                    pos && pos.speed_kmh !== null && ageClass(pos.age_s) === '' ? h('div', { class: 'lv-item-speed', text: pos.speed_kmh + ' կմ/ժ' }) : null,
                    t.alerts.count ? h('span', { class: 'lv-badge', title: 'Այսօրվա ահազանգեր' }, icon('fa-bell'), String(t.alerts.count)) : null),
                h('div', { class: 'lv-item-meta' }, ...meta));
            item.addEventListener('click', () => select(t.car_code));
            item.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(t.car_code); } });
            return item;
        }));
    }

    // ---------- карта ----------
    function initMap() {
        if (typeof window.L === 'undefined' || typeof window.RoutesBasemap === 'undefined') {
            $('lvMap').textContent = 'Քարտեզը չբեռնվեց։ Թարմացրեք էջը։';
            return;
        }
        state.map = L.map('lvMap', { zoomSnap: 0.5, zoomControl: false });
        L.control.zoom({ zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' }).addTo(state.map);
        RoutesBasemap.add(state.map);
        state.map.setView(YEREVAN, 11);
        state.layer = L.layerGroup().addTo(state.map);
    }

    function markerIcon(t) {
        const [, cls] = STATE[t.state] || STATE.standing;
        const el = h('div', { class: 'lv-marker ' + cls + (t.car_code === state.selected ? ' is-selected' : '') });
        const heading = t.position ? num(t.position.heading) : null;
        if (heading !== null && t.state === 'moving') {
            const arrow = h('span', { class: 'lv-marker-arrow' });
            arrow.style.transform = 'rotate(' + heading + 'deg)';
            el.append(arrow);
        }
        el.append(h('span', { class: 'lv-marker-body' }, icon('fa-truck')), h('span', { class: 'lv-marker-plate', text: t.car_code }));
        if (t.alerts.count) el.append(h('span', { class: 'lv-marker-count', text: String(t.alerts.count) }));
        return L.divIcon({ html: el.outerHTML, className: '', iconSize: [34, 34], iconAnchor: [17, 17] });
    }

    function renderMarkers(trucks) {
        if (!state.map) return;
        const seen = new Set();
        for (const t of trucks) {
            if (!t.position) continue;
            seen.add(t.car_code);
            const ll = [t.position.lat, t.position.lon];
            let m = state.markers.get(t.car_code);
            if (!m) {
                m = L.marker(ll, { keyboard: false, riseOnHover: true }).addTo(state.map);
                m.on('click', () => select(t.car_code));
                state.markers.set(t.car_code, m);
            } else {
                m.setLatLng(ll);
            }
            m.setIcon(markerIcon(t));
            m.setZIndexOffset(t.car_code === state.selected ? 1000 : 0);
            m.bindTooltip(tip(t.car_code + (t.name ? ' · ' + t.name : '')), { direction: 'top', offset: [0, -16] });
        }
        for (const [car, m] of state.markers) {
            if (!seen.has(car)) { m.remove(); state.markers.delete(car); }
        }
        if (!state.fitted && seen.size) {
            const pts = trucks.filter(t => t.position).map(t => [t.position.lat, t.position.lon]);
            if (state.data.depot) pts.push(state.data.depot);
            state.map.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 14 });
            state.fitted = true;
        }
        if (!state.zone && state.data.center_zone && state.data.center_zone.length >= 3) {   // малый центр — тонкий контур
            state.zone = L.polygon(state.data.center_zone, { color: '#ffb547', weight: 1, dashArray: '4 4', fill: false, interactive: false })
                .addTo(state.map);
        }
    }

    function renderTruckLayer(t) {
        if (!state.layer) return;
        state.layer.clearLayers();
        if (!t) return;
        if (state.data.depot) {
            L.circleMarker(state.data.depot, { radius: 8, color: '#e8edf4', weight: 2, fillColor: '#38bdf8', fillOpacity: 1 })
                .bindTooltip(tip('Պահեստ')).addTo(state.layer);
        }
        if (t.track && t.track.length > 1) {
            L.polyline(t.track, { color: '#38bdf8', weight: 4, opacity: 0.85 }).addTo(state.layer);
        }
        for (const s of t.stops || []) {
            if (num(s.lat) === null || num(s.lon) === null) continue;
            const [label, color] = STORE[s.status] || STORE.pending;
            const text = (s.name || s.stop_id) + ' — ' + label + (s.planned_eta ? ' · պլան՝ ' + hm(s.planned_eta) : '')
                + (s.arrive ? ' · ժամանում՝ ' + hm(s.arrive) : '');
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0e1116', weight: 2, fillColor: color, fillOpacity: 1 })
                .bindTooltip(tip(text)).addTo(state.layer);
        }
        for (const a of t.alerts_log || []) {
            if (num(a.lat) === null || num(a.lon) === null) continue;
            L.circleMarker([a.lat, a.lon], { radius: 5, color: '#ff6b79', weight: 2, fill: false })
                .bindTooltip(tip((ALERT[a.kind] || ['', a.kind])[1] + ' · ' + hm(a.from))).addTo(state.layer);
        }
    }

    // ---------- карточка ----------
    function field(label, value, sub, cls, wide) {
        const dd = h('dd', { class: cls || null });
        dd.append(...(Array.isArray(value) ? value : [value]));
        if (sub) dd.append(h('small', { text: sub }));
        return h('div', { class: wide ? 'is-wide' : null }, h('dt', { text: label }), dd);
    }

    function alertText(a) {
        const [, title] = ALERT[a.kind] || ['', a.kind];
        const when = hm(a.from) + (a.to ? '–' + hm(a.to) : ' — հիմա');
        let more = '';
        if (a.kind === 'speed') more = ', մինչև ' + a.max_kmh + ' կմ/ժ';
        else if (a.kind === 'stop') more = ', ' + a.minutes + ' րոպե' + (a.lunch ? ' (ճաշի ժամին)' : '');
        else if (a.kind === 'no_contact') more = ', ' + a.minutes + ' րոպե';
        else if (a.kind === 'gps') more = a.gps === 'no_permission' ? ' (թույլտվություն չկա)' : '';
        return [title + more, when];
    }

    function renderCard() {
        const t = state.detail || (state.data && state.data.trucks.find(x => x.car_code === state.selected));
        $('lvCard').hidden = !t;
        if (!t) return;
        const [label, cls] = STATE[t.state] || STATE.standing;
        $('lvPlate').textContent = t.car_code;
        $('lvSub').textContent = [t.name, t.planned ? null : 'պլանում չէ'].filter(Boolean).join(' · ');
        $('lvState').replaceChildren(h('span', { class: 'lv-dot ' + cls }), label);

        const active = (t.alerts_log || []).filter(a => a.active);
        $('lvActive').hidden = !active.length;
        $('lvActive').replaceChildren(...active.map(a => {
            const [text] = alertText(a);
            return h('div', null, icon((ALERT[a.kind] || ['fa-bell'])[0]), h('span', { text }));
        }));

        const p = t.position;
        const rows = [];
        rows.push(field('Դիրքը', p ? [ago(p.age_s)] : '—', p ? 'GPS՝ ' + hms(p.at) + ' · ' + p.lat.toFixed(5) + ', ' + p.lon.toFixed(5) : 'GPS կետ դեռ չկա', p ? ageClass(p.age_s) : 'is-mute'));
        rows.push(field('Արագություն', p && p.speed_kmh !== null ? p.speed_kmh + ' կմ/ժ' : '—',
            p && p.heading !== null ? 'ուղղություն՝ ' + p.heading + '°' : null));
        const crew = [t.driver ? 'Վարորդ՝ ' + t.driver : null, t.helper ? 'Առաքիչ՝ ' + t.helper : null].filter(Boolean).join(' · ');
        const term = (t.drivers || [])[0];
        rows.push(field('Վարորդ / առաքիչ', crew || '—', term && term !== t.driver ? 'Տերմինալում՝ ' + term : null, null, true));
        rows.push(field('Խանութներ', t.stores.done + ' / ' + t.stores.total,
            t.stores.in_progress ? 'ընթացքի մեջ՝ ' + t.stores.in_progress : null));
        rows.push(field('Այսօր, կմ (GPS)', fmt(t.km, 1)));
        rows.push(field('Վառելիք', t.fuel_l === null ? '—' : ['≈ ' + fmt(t.fuel_l, 1) + ' լ', h('span', { class: 'lv-est', text: 'հաշվարկ' })],
            t.fuel_l === null ? 'մեքենայի ծախսը նշված չէ կարգավորումներում' : null));
        const ld = t.load;
        rows.push(field('Բեռի մնացորդ', fmt(ld.remaining_kg) + ' կգ',
            [ld.trips_gone ? 'բեռնված՝ ' + fmt(ld.loaded_kg) + ' կգ, առաքված՝ ' + fmt(ld.delivered_kg) + ' կգ'
                + (ld.trips > 1 ? ' · երթ ' + ld.trips_gone + '/' + ld.trips : '') : 'մեքենան դեռ չի մեկնել պահեստից',
            ld.unweighed_lines ? ld.unweighed_lines + ' տող առանց քաշի' : null].filter(Boolean).join(' · ')));
        if (t.next) {
            const d = delayText(t.next.delay_min);
            rows.push(field('Հաջորդ խանութը', t.next.name || t.next.stop_id,
                (t.next.here ? 'տեղում է' : 'ժամանում ≈ ' + hm(t.next.eta)) + (t.next.planned_eta ? ' · պլան՝ ' + hm(t.next.planned_eta) : '')
                + (d ? ' · ' + d[0] : ''), d && d[1] !== 'is-ok' ? d[1] : null, true));
        }
        rows.push(field('Վերադարձ պահեստ', t.return_eta ? '≈ ' + hm(t.return_eta) : '—'));
        rows.push(field('Վերջին կապը', t.last_contact ? hms(t.last_contact) : '—', t.contact_age_s !== null ? ago(t.contact_age_s) : null,
            ageClass(t.contact_age_s)));
        const dv = t.device;
        rows.push(field('Տերմինալ', dv ? [(dv.battery !== null ? dv.battery + '%' : '—'), dv.charging ? ' ⚡' : '']
            : '—', dv ? ['GPS՝ ' + (GPS[dv.gps] || '—'), 'ինտերնետ՝ ' + (NET[dv.net] || '—'), 'APK ' + (dv.app || '—'), 'տվյալ՝ ' + hm(dv.at)].join(' · ')
            : 'տերմինալը չի ուղարկում իր վիճակը (հին տարբերակ)', dv && dv.gps && dv.gps !== 'on' ? 'is-bad' : null, true));
        $('lvGrid').replaceChildren(...rows);

        $('lvStopsBox').hidden = !t.stops;
        $('lvStops').replaceChildren(...(t.stops || []).map(s => {
            const [lab, color] = STORE[s.status] || STORE.pending;
            const dot = h('span', { class: 'lv-dot' });
            dot.style.background = color;
            return h('li', { title: lab }, dot, h('span', { text: (s.name || s.stop_id) + ' · ' + lab }),
                h('span', { class: 'when', text: (s.arrive ? hm(s.arrive) : '') + (s.planned_eta ? ' (պլան՝ ' + hm(s.planned_eta) + ')' : '') }));
        }));
        const log = t.alerts_log || [];
        $('lvLogBox').hidden = !t.stops;
        $('lvLog').replaceChildren(...(log.length ? log.map(a => {
            const [text, when] = alertText(a);
            return h('li', { class: a.active ? 'is-active' : null }, icon((ALERT[a.kind] || ['fa-bell'])[0]), h('span', { text }), h('span', { class: 'when', text: when }));
        }) : [h('li', null, h('span'), h('span', { text: 'Ահազանգ չկա' }), h('span'))]));
    }

    // ---------- данные ----------
    const query = () => (state.date ? '?date=' + encodeURIComponent(state.date) : '');

    async function refresh() {
        if (state.busy) return;
        state.busy = true;
        try {
            const data = await api('/api/routes/live' + query());
            state.data = data;
            if (!state.date) $('lvDate').value = data.date;
            $('lvToday').hidden = !state.date;
            if (state.selected && !data.trucks.some(t => t.car_code === state.selected)) { state.selected = null; state.detail = null; }
            if (state.selected) {
                const sep = query() ? '&' : '?';
                const one = await api('/api/routes/live/truck' + query() + sep + 'car=' + encodeURIComponent(state.selected));
                state.detail = one.truck;
            }
            renderSummary(data.trucks);
            renderList(data.trucks);
            renderMarkers(data.trucks);
            renderTruckLayer(state.detail);
            renderCard();
            showError('');
            $('lvLive').hidden = false;
            $('lvLive').classList.toggle('is-paused', !!state.date);
            $('lvLiveText').textContent = 'Թարմացվել է ' + hms(data.now);
        } catch (e) {
            showError(e.message);
        } finally {
            state.busy = false;
        }
    }

    function select(car) {
        state.selected = state.selected === car ? null : car;
        state.detail = null;
        renderCard();
        renderTruckLayer(null);
        if (state.data) { renderList(state.data.trucks); renderMarkers(state.data.trucks); }
        refresh().then(() => {
            const t = state.detail;
            if (t && t.position && state.map) state.map.panTo([t.position.lat, t.position.lon]);
            if (t && window.matchMedia('(max-width: 899px)').matches) $('lvCard').scrollIntoView({ block: 'start' });
        });
    }

    // опрос — только пока вкладка видна (телефон в кармане не тратит трафик); прошлый день не обновляется
    function schedule() {
        clearInterval(state.timer);
        state.timer = null;
        if (document.visibilityState === 'visible' && !state.date) state.timer = setInterval(refresh, POLL_MS);
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refresh();
        schedule();
    });

    $('lvDate').addEventListener('change', () => {
        const v = $('lvDate').value;
        const today = state.data && state.data.now ? state.data.now.slice(0, 10) : '';
        state.date = v && v !== today ? v : '';
        state.fitted = false;
        state.selected = null;
        state.detail = null;
        refresh();
        schedule();
    });
    $('lvToday').addEventListener('click', () => { $('lvDate').value = ''; $('lvDate').dispatchEvent(new Event('change')); });

    initMap();
    refresh();
    schedule();
})();

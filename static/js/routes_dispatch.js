/* «Развоз» /routes/dispatch — план развоза на завтра (docs/plans/dispatch-plan.md).
   Данные: GET /api/routes/dispatch?date=…; «Собрать рейсы» — POST /api/routes/dispatch/build;
   правки логиста — POST /api/routes/dispatch/edit (move | pin | unpin | exclude | include, с номером
   черновика rev); «Начать заново» — POST /api/routes/dispatch/reset; ручная точка магазина —
   POST /api/routes/geo-override; «План и факт» — GET /api/routes/dispatch/fact?date=….
   Безопасность: всё, что пришло из ERP (магазины, адреса, менеджеры, машины), выводится только через
   esc() или textContent — в том числе в попапах карты, листах для водителей и Excel. POST — только JSON. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const NB = ' ';
    const kgText = (kg) => { const n = num(kg) || 0; return n >= 1000 ? fmt(n / 1000, 1) + NB + 'т' : fmt(n) + NB + 'кг'; };
    const money = (v) => fmt(Math.round(num(v) || 0)) + NB + 'драм';
    function plural(n, one, few, many) {
        const a = Math.abs(Math.trunc(n)) % 100, b = a % 10;
        if (a > 10 && a < 20) return many;
        if (b > 1 && b < 5) return few;
        if (b === 1) return one;
        return many;
    }
    const pl = (n, one, few, many) => fmt(n) + NB + plural(n, one, few, many);
    const WD_ACC = { 1: 'понедельник', 2: 'вторник', 3: 'среду', 4: 'четверг', 5: 'пятницу', 6: 'субботу', 7: 'воскресенье' };
    const WD_SHORT = { 1: 'пн', 2: 'вт', 3: 'ср', 4: 'чт', 5: 'пт', 6: 'сб', 7: 'вс' };
    const dateRu = (s) => (typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—');
    const wdOf = (s) => { const d = new Date(s + 'T12:00:00'); return Number.isNaN(d.getTime()) ? null : (d.getDay() || 7); };
    // Цвета машин: тот же ряд, что у менеджеров в «Обзоре» (различимы и при дальтонизме)
    const COLORS = ['#18c1fc', '#fe904d', '#b0a2ff', '#a77601', '#397be9', '#14cfa3', '#0d9298', '#b8b90c', '#ae55c1', '#37981b'];
    const COORD = { erp: 'адрес из ERP', gps: 'по GPS визитов', manual: 'поставлена вручную', none: 'нет точки' };
    const YEREVAN = [40.1792, 44.4991];

    const state = {
        day: null, data: null, busy: false,
        map: null, layers: null, mapFailed: false,
        pickMap: null, pickMarker: null, pickCid: null,
    };

    // ---------- Сервер ----------
    const HTTP_TEXT = {
        400: 'Сервер не принял запрос.', 401: 'Требуется вход в систему.', 403: 'Доступ запрещён — раздел только для администратора.',
        404: 'Не найдено.', 409: 'План изменили в другой вкладке — обновите страницу.', 415: 'Сервер не принял запрос.',
        500: 'Внутренняя ошибка сервера.', 503: 'База данных ERP недоступна.',
    };
    async function api(method, url, body) {
        const opts = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let resp;
        try { resp = await fetch(url, opts); } catch (e) {
            throw Object.assign(new Error('Нет связи с сервером.'), { status: 0, data: null });
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (resp.ok && isObj(data) && data.success === true) return data;
        const d = isObj(data) ? data : null;
        const msg = (d && typeof d.error === 'string' && d.error.trim())
            || (d && isObj(d.errors) ? Object.values(d.errors).map(String).join('; ') : '')
            || HTTP_TEXT[resp.status] || ('Ошибка сервера (код ' + resp.status + ').');
        throw Object.assign(new Error(msg), { status: resp.status, data: d });
    }

    function announce(msg) {
        const el = $('dpStatus');
        el.textContent = '';
        setTimeout(() => { el.textContent = msg; }, 40);
    }
    let toastTimer = null;
    function toast(msg) {
        let el = $('dpToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'dpToast';
            el.className = 'dp-toast';
            el.setAttribute('role', 'status');
            $('rtDispatch').appendChild(el);
        }
        el.textContent = msg;
        el.classList.add('is-on');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('is-on'), 4200);
        announce(msg);
    }
    function showActionError(err) {
        $('dpActionErrorText').textContent = err.message || String(err);
        $('dpActionReload').classList.toggle('d-none', !(err.status === 409));
        $('dpActionError').classList.remove('d-none');
        $('dpActionError').focus();
    }
    const hideActionError = () => $('dpActionError').classList.add('d-none');

    // ---------- Загрузка ----------
    async function load(day, refresh) {
        $('dpLoading').hidden = false;
        $('dpLoading').classList.remove('d-none');
        $('dpLoadError').classList.add('d-none');
        try {
            const q = new URLSearchParams();
            if (day) q.set('date', day);
            if (refresh) q.set('refresh', '1');
            const data = await api('GET', '/api/routes/dispatch' + (q.toString() ? '?' + q : ''));
            setData(data);
            $('dpBody').hidden = false;
        } catch (e) {
            $('dpLoadErrorText').textContent = e.message;
            $('dpLoadError').classList.remove('d-none');
        } finally {
            $('dpLoading').classList.add('d-none');
        }
    }

    function setData(data) {
        state.data = data;
        state.day = data.day;
        const url = new URL(window.location.href);
        url.searchParams.set('date', data.day);
        window.history.replaceState(null, '', url);
        render();
    }

    // ---------- Отрисовка ----------
    function render() {
        const d = state.data;
        const wd = d.weekday;
        $('dpTitle').textContent = 'Развоз на ' + (WD_ACC[wd] || '') + ', ' + dateRu(d.day);
        document.title = 'Развоз на ' + dateRu(d.day) + ' — Sales Dashboard';
        $('dpDate').value = d.day;
        const od = d.order_dates;
        const ordersDays = od.since === od.until ? 'за ' + WD_SHORT[wdOf(od.since)] + ' ' + dateRu(od.since)
            : 'с ' + WD_SHORT[wdOf(od.since)] + ' ' + dateRu(od.since) + ' по ' + WD_SHORT[wdOf(od.until)] + ' ' + dateRu(od.until);
        $('dpDateNote').textContent = (d.is_past ? 'Прошедшая дата — для разбора. ' : '') + 'Везём заказы ' + ordersDays
            + ' · данные ERP на ' + (d.data_as_of || '').slice(11, 16);
        $('dpTomorrow').hidden = d.day === d.default_day;
        renderProblems();
        renderTrucks();
        renderOrders();
        renderNoCoords();
        renderOrderLists();
        const plan = d.plan;
        $('dpBuildText').textContent = plan ? 'Собрать рейсы заново' : 'Собрать рейсы';
        $('dpReset').hidden = !plan;
        $('dpBuild').disabled = !d.trucks.some(t => t.ready) || !d.depot;
        $('dpBuildNote').textContent = plan
            ? 'Закреплённые рейсы останутся как есть, остальное программа разложит заново'
            : '≈ 5 секунд, ERP только читается';
        $('dpStep1').classList.toggle('is-done', !!plan);
        $('dpStep2').classList.toggle('is-done', !!plan);
        $('dpStep3').hidden = !plan;
        if (plan) renderPlan(plan);
        $('dpFact').hidden = !d.is_past;
        if (!d.is_past) $('dpFactOut').textContent = '';
    }

    function renderProblems() {
        const box = $('dpProblems');
        box.textContent = '';
        (state.data.problems || []).forEach(p => {
            const div = document.createElement('div');
            div.className = 'rt-alert is-warn';
            div.innerHTML = '<i class="fas fa-circle-exclamation" aria-hidden="true"></i><span class="rt-alert-text"></span>';
            div.querySelector('.rt-alert-text').textContent = p.text + ' — без этого рейсы не собрать.';
            if (typeof p.link === 'string' && /^\/(?!\/)[^\s\\]*$/.test(p.link)) {
                const a = document.createElement('a');
                a.href = p.link;
                a.className = 'rt-btn rt-btn-ghost rt-btn-sm';
                a.textContent = 'Открыть настройки';
                div.appendChild(a);
            }
            box.appendChild(div);
        });
    }

    function truckLabel(t) {
        return (t.name ? t.name + ' · ' : '') + t.car_code;
    }
    function truckColor(code) {
        const list = state.data.trucks.filter(t => t.ready).map(t => t.car_code);
        const i = list.indexOf(code);
        return COLORS[i >= 0 ? i % COLORS.length : COLORS.length - 1];
    }
    const truckBy = (code) => state.data.trucks.find(t => t.car_code === code) || { car_code: code, name: null };

    function renderTrucks() {
        const box = $('dpTrucks');
        box.querySelectorAll('.dp-truck, .dp-truck-empty').forEach(x => x.remove());
        const ready = state.data.trucks.filter(t => t.ready);
        if (!ready.length) {
            const p = document.createElement('p');
            p.className = 'dp-truck-empty';
            p.innerHTML = 'Машины не заполнены. <a href="/routes/settings#trucks">Укажите тоннаж и расход в настройках →</a>';
            box.appendChild(p);
        }
        state.data.trucks.forEach(t => {
            const lab = document.createElement('label');
            lab.className = 'dp-truck' + (t.ready ? '' : ' is-off');
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = t.car_code;
            cb.checked = !!t.selected;
            cb.disabled = !t.ready;
            const sw = document.createElement('span');
            sw.className = 'rt-dot';
            sw.style.background = t.ready ? truckColor(t.car_code) : 'transparent';
            sw.setAttribute('aria-hidden', 'true');
            const txt = document.createElement('span');
            txt.className = 'dp-truck-t';
            const nm = document.createElement('b');
            nm.textContent = truckLabel(t);
            const sub = document.createElement('span');
            sub.className = 'dp-truck-sub';
            sub.textContent = t.ready ? fmt(t.capacity_kg / 1000, 1) + NB + 'т · ' + fmt(t.l100, 1) + NB + 'л на 100 км'
                : 'не заполнены тоннаж или расход';
            txt.append(nm, sub);
            lab.append(cb, sw, txt);
            box.appendChild(lab);
        });
    }
    const selectedTrucks = () => [...$('dpTrucks').querySelectorAll('input[type="checkbox"]:checked')].map(x => x.value);

    function renderOrders() {
        const o = state.data.orders;
        $('dpOrders').innerHTML = o.count
            ? '<b>' + esc(pl(o.count, 'заказ', 'заказа', 'заказов')) + '</b> в ' + esc(pl(o.customers, 'магазин', 'магазина', 'магазинов'))
              + ' · <b>' + esc(kgText(o.kg)) + '</b> · ' + esc(money(o.revenue))
            : 'Заказов на этот день пока нет.';
        const facts = $('dpFacts');
        facts.textContent = '';
        const add = (cls, text, btn) => {
            const li = document.createElement('li');
            li.className = cls;
            const s = document.createElement('span');
            s.textContent = text;
            li.appendChild(s);
            if (btn) li.appendChild(btn);
            facts.appendChild(li);
        };
        const opener = (id, label) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'rt-linkbtn';
            b.textContent = label;
            b.addEventListener('click', () => { const el = $(id); el.open = true; el.scrollIntoView({ block: 'start', behavior: 'smooth' }); el.querySelector('summary').focus(); });
            return b;
        };
        if (o.no_coords) add('is-warn', 'Без точки на карте: ' + pl(o.no_coords, 'магазин', 'магазина', 'магазинов') + ' (' + kgText(o.no_coords_kg) + ') — в рейсы не попадут. ', opener('dpNoCoords', 'Поставить точки'));
        const bl = state.data.backlog || [];
        const added = bl.filter(x => x.added).length;
        if (bl.length) add('', 'Не отгружены с прошлых дней: ' + pl(bl.length, 'заказ', 'заказа', 'заказов') + (added ? ', добавлено в развоз ' + added : '') + '. ', opener('dpBacklog', 'Посмотреть'));
        if (o.excluded) add('', 'Не везём сегодня: ' + pl(o.excluded, 'заказ', 'заказа', 'заказов') + '. ', opener('dpExcluded', 'Посмотреть'));
        if (o.shipped_before) add('is-mute', 'Уже отгружено раньше: ' + pl(o.shipped_before, 'заказ', 'заказа', 'заказов') + ' — в плане их нет.');
        if (o.self_delivery) add('is-mute', 'Менеджер развозит сам: ' + pl(o.self_delivery, 'заказ', 'заказа', 'заказов') + ' (' + kgText(o.self_delivery_kg) + ') — машины парка их не везут.');
    }

    // ---------- Магазины без точки ----------
    function renderNoCoords() {
        const list = state.data.stops_no_coords || [];
        const box = $('dpNoCoords');
        box.hidden = !list.length;
        $('dpNoCoordsNote').textContent = list.length ? pl(list.length, 'магазин', 'магазина', 'магазинов') : '';
        const ul = $('dpPickList');
        ul.textContent = '';
        if (!list.some(s => s.customer_id === state.pickCid)) resetPick();
        list.forEach(s => {
            const li = document.createElement('li');
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'dp-pickitem';
            b.setAttribute('aria-pressed', String(s.customer_id === state.pickCid));
            b.innerHTML = '<b></b><span class="dp-pick-addr"></span><span class="dp-pick-kg"></span>';
            b.querySelector('b').textContent = s.name || s.code;
            b.querySelector('.dp-pick-addr').textContent = (s.code ? s.code + ' · ' : '') + (s.address || 'адреса в ERP нет');
            b.querySelector('.dp-pick-kg').textContent = kgText(s.kg) + ' · ' + (s.agent_name || s.agent_code || '');
            b.addEventListener('click', () => choosePick(s));
            li.appendChild(b);
            ul.appendChild(li);
        });
    }
    function resetPick() {
        state.pickCid = null;
        $('dpPickLabel').textContent = 'Сначала выберите магазин слева';
        $('dpPickCoord').value = '';
        $('dpPickCoord').disabled = true;
        $('dpPickSave').disabled = true;
        $('dpPickErr').textContent = '';
        if (state.pickMarker) { state.pickMarker.remove(); state.pickMarker = null; }
    }
    function choosePick(s) {
        state.pickCid = s.customer_id;
        $('dpPickList').querySelectorAll('.dp-pickitem').forEach((b, i) => b.setAttribute('aria-pressed', String(state.data.stops_no_coords[i].customer_id === s.customer_id)));
        $('dpPickLabel').textContent = 'Точка для «' + (s.name || s.code) + '»: нажмите на карте или вставьте координаты';
        $('dpPickCoord').disabled = false;
        $('dpPickCoord').value = '';
        $('dpPickSave').disabled = true;
        $('dpPickErr').textContent = '';
        ensurePickMap();
        $('dpPickCoord').focus();
    }
    function ensurePickMap() {
        if (state.pickMap || typeof window.L === 'undefined') {
            if (state.pickMap) state.pickMap.invalidateSize();
            return;
        }
        const map = L.map($('dpPickMap'), { zoomSnap: 0.5, scrollWheelZoom: false });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
        map.setView(state.data.depot ? [state.data.depot.lat, state.data.depot.lon] : YEREVAN, 11);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        map.on('click', (e) => {
            if (state.pickCid === null) return;
            setPick(e.latlng.lat, e.latlng.lng);
        });
        state.pickMap = map;
    }
    function setPick(lat, lon) {
        $('dpPickCoord').value = lat.toFixed(6) + ', ' + lon.toFixed(6);
        $('dpPickSave').disabled = false;
        $('dpPickErr').textContent = '';
        if (state.pickMap) {
            if (!state.pickMarker) state.pickMarker = L.marker([lat, lon], { draggable: true, keyboard: false }).addTo(state.pickMap)
                .on('dragend', (ev) => { const p = ev.target.getLatLng(); setPick(p.lat, p.lng); });
            else state.pickMarker.setLatLng([lat, lon]);
        }
    }
    function parseCoord(s) {
        const m = /^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/.exec(String(s || ''));
        if (!m) return null;
        const lat = parseFloat(m[1].replace(',', '.')), lon = parseFloat(m[2].replace(',', '.'));
        if (!(lat >= 38.8 && lat <= 41.4 && lon >= 43.4 && lon <= 46.7)) return null;
        return [lat, lon];
    }
    async function savePick() {
        const p = parseCoord($('dpPickCoord').value);
        if (!p) { $('dpPickErr').textContent = 'Нужны широта и долгота в Армении, например 40.17920, 44.49910'; return; }
        if (state.pickCid === null || state.busy) return;
        state.busy = true;
        $('dpPickSave').disabled = true;
        try {
            await api('POST', '/api/routes/geo-override', { customer_id: state.pickCid, lat: p[0], lon: p[1] });
            toast('Точка сохранена. Магазин можно везти — он появится в «ещё не в рейсах» или при сборке рейсов.');
            resetPick();
            await reloadQuiet();
        } catch (e) {
            $('dpPickErr').textContent = e.message;
            $('dpPickSave').disabled = false;
        } finally { state.busy = false; }
    }
    async function reloadQuiet() {
        const data = await api('GET', '/api/routes/dispatch?date=' + encodeURIComponent(state.day));
        setData(data);
    }

    // ---------- Заказы прошлых дней и исключённые ----------
    function orderLine(o, btnText, onClick) {
        const li = document.createElement('li');
        li.className = 'dp-oitem';
        const t = document.createElement('span');
        t.className = 'dp-oitem-t';
        const b = document.createElement('b');
        b.textContent = o.name || o.code || ('клиент ' + o.customer_id);
        const s = document.createElement('span');
        s.textContent = (o.code ? o.code + ' · ' : '') + 'заказ ' + (o.doc_num || '') + (o.order_date ? ' от ' + dateRu(o.order_date) : '')
            + ' · ' + kgText(o.kg) + ' · ' + money(o.revenue);
        t.append(b, s);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        btn.textContent = btnText;
        btn.addEventListener('click', onClick);
        li.append(t, btn);
        return li;
    }
    function needPlan() {
        if (state.data.plan) return true;
        showActionError(new Error('Сначала нажмите «Собрать рейсы» — правки сохраняются в плане дня.'));
        return false;
    }
    function renderOrderLists() {
        const bl = state.data.backlog || [];
        $('dpBacklog').hidden = !bl.length;
        $('dpBacklogNote').textContent = bl.length ? pl(bl.length, 'заказ', 'заказа', 'заказов') : '';
        const ul = $('dpBacklogList');
        ul.textContent = '';
        bl.forEach(o => ul.appendChild(orderLine(o, o.added ? 'Убрать из развоза' : 'Добавить в развоз',
            () => { if (needPlan()) edit({ action: o.added ? 'exclude' : 'include', order: o.isn }, o.added ? 'Заказ убран из развоза' : 'Заказ добавлен — нажмите «Собрать рейсы заново» или перенесите точку в рейс'); })));
        const ex = state.data.excluded || [];
        $('dpExcluded').hidden = !ex.length;
        $('dpExcludedNote').textContent = ex.length ? pl(ex.length, 'заказ', 'заказа', 'заказов') : '';
        const ul2 = $('dpExcludedList');
        ul2.textContent = '';
        ex.forEach(o => ul2.appendChild(orderLine(o, 'Вернуть', () => edit({ action: 'include', order: o.isn }, 'Заказ возвращён: точка — в «ещё не в рейсах» или в своём рейсе'))));
    }

    // ---------- Рейсы ----------
    function deltaText(delta) {
        const v = num(delta);
        if (v === null || Math.abs(v) < 0.05) return 'км плана не изменились';
        return v > 0 ? 'план стал длиннее на ' + fmt(v, 1) + NB + 'км' : 'план стал короче на ' + fmt(-v, 1) + NB + 'км';
    }

    function renderPlan(plan) {
        const sm = plan.summary, base = plan.baseline;
        let lead = pl(sm.trips, 'рейс', 'рейса', 'рейсов') + ' на ' + pl(sm.trucks, 'машине', 'машинах', 'машинах')
            + ', ≈ ' + fmt(sm.km) + NB + 'км и ' + fmt(sm.liters) + NB + 'л дизеля';
        if (base && num(base.km) !== null) {
            const diff = Math.round(base.km - sm.km);
            if (diff > 0) lead += ' — на ' + fmt(diff) + NB + 'км меньше, чем если развозить по менеджерам';
            else if (diff < 0) lead += ' — на ' + fmt(-diff) + NB + 'км больше, чем если развозить по менеджерам';
            else lead += ' — столько же, сколько если развозить по менеджерам';
        }
        $('dpMainLead').textContent = lead + '.';
        const list = $('dpMainList');
        list.textContent = '';
        const item = (cls, ico, text) => {
            const li = document.createElement('li');
            li.className = cls;
            li.innerHTML = '<span class="ico"><i class="fas ' + ico + '" aria-hidden="true"></i></span><span class="txt"></span>';
            li.querySelector('.txt').textContent = text;
            list.appendChild(li);
        };
        item('is-good', 'fa-box', 'В рейсах ' + pl(sm.stops, 'магазин', 'магазина', 'магазинов') + ', груз ≈ ' + kgText(sm.kg) + '.');
        if (base && base.liters !== null) {
            const dl = Math.round(base.liters - sm.liters);
            if (dl > 0) item('is-good', 'fa-gas-pump', 'Дизеля ≈ на ' + fmt(dl) + NB + 'л меньше, чем по менеджерам (' + fmt(base.liters) + ' → ' + fmt(sm.liters) + NB + 'л).');
        }
        const late = [];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => { if (tr.over_time) late.push(truckLabel(t) + ', рейс ' + (i + 1)); }));
        if (late.length) item('is-bad', 'fa-clock', 'Не успевают до ' + state.data.work_end + ': ' + late.join('; ') + ' — нужна ещё машина или рейс на другой день.');
        if (plan.unassigned.length) item('is-warn', 'fa-circle-exclamation', pl(plan.unassigned.length, 'магазин', 'магазина', 'магазинов') + ' ещё не в рейсах — перенесите их в рейс ниже или нажмите «Собрать рейсы заново».');
        const nc = state.data.orders.no_coords;
        if (nc) item('is-warn', 'fa-location-dot', pl(nc, 'магазин', 'магазина', 'магазинов') + ' без точки на карте — поставьте точки в шаге 2.');
        renderOverflow(plan);
        renderUnassigned(plan);
        renderTruckCards(plan);
        renderBaseline(plan);
        if (!$('dpMapBox').open) return;
        drawMap();
    }

    function renderOverflow(plan) {
        const box = $('dpOverflow');
        box.textContent = '';
        const bad = [];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => {
            const why = [];
            if (tr.over_time) why.push('не успевает до ' + state.data.work_end);
            if (tr.over_capacity) why.push('перегруз: ' + kgText(tr.kg) + ' при тоннаже ' + kgText(t.capacity_kg));
            if (tr.no_truck) why.push('машина сегодня не работает');
            if (why.length) bad.push(truckLabel(t) + ', рейс ' + (i + 1) + ' (' + kgText(tr.kg) + '): ' + why.join(', '));
        }));
        if (!bad.length && !plan.unassigned.length) return;
        const div = document.createElement('div');
        div.className = 'rt-alert is-warn';
        div.innerHTML = '<i class="fas fa-triangle-exclamation" aria-hidden="true"></i><div class="rt-alert-text"><b>Не помещается — нужна ещё машина или рейс</b><ul></ul></div>';
        const ul = div.querySelector('ul');
        bad.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
        if (plan.unassigned.length) {
            const li = document.createElement('li');
            li.textContent = 'Не в рейсах: ' + pl(plan.unassigned.length, 'магазин', 'магазина', 'магазинов') + ', ' + kgText(plan.overflow.unassigned_kg);
            ul.appendChild(li);
        }
        box.appendChild(div);
    }

    // Все рейсы плана — для списка «Перенести в…»
    function tripOptions(currentTrip) {
        const opts = [['', 'Перенести в…']];
        state.data.plan.trucks.forEach(t => t.trips.forEach((tr, i) => {
            if (tr.id !== currentTrip) opts.push(['t:' + tr.id, truckLabel(t) + ' · рейс ' + (i + 1)]);
        }));
        state.data.trucks.filter(t => t.selected).forEach(t => opts.push(['n:' + t.car_code, 'Новый рейс · ' + truckLabel(t)]));
        if (currentTrip !== null) opts.push(['u:', 'Убрать из рейсов']);
        return opts;
    }
    function moveSelect(stop, tripId, label) {
        const sel = document.createElement('select');
        sel.className = 'rt-select dp-move';
        sel.setAttribute('aria-label', 'Перенести «' + (stop.name || stop.code) + '» ' + label);
        tripOptions(tripId).forEach(([v, t]) => sel.add(new Option(t, v)));
        sel.addEventListener('change', () => {
            const v = sel.value;
            if (!v) return;
            const body = { action: 'move', customer_id: stop.customer_id, from_trip: tripId, to_trip: null, truck: null };
            if (v.startsWith('t:')) body.to_trip = Number(v.slice(2));
            else if (v.startsWith('n:')) body.truck = v.slice(2);
            edit(body, '«' + (stop.name || stop.code) + '» перенесён');
        });
        return sel;
    }

    function stopRow(stop, idx, tripId) {
        const tr = document.createElement('tr');
        const cell = (cls, label) => { const td = document.createElement('td'); if (cls) td.className = cls; if (label) td.setAttribute('data-label', label); tr.appendChild(td); return td; };
        cell('dp-no w-half', '№').textContent = String(idx);
        const nm = cell('rt-cell-name', 'Магазин');
        const b = document.createElement('b');
        b.textContent = stop.name || stop.code;
        const sub = document.createElement('span');
        sub.className = 'dp-sub';
        sub.textContent = (stop.code || '') + (stop.address ? ' · ' + stop.address : '');
        nm.append(b, sub);
        if (stop.coord_source === 'manual') {
            const bd = document.createElement('span');
            bd.className = 'rt-badge b-manual';
            bd.textContent = 'точка вручную';
            nm.appendChild(bd);
        }
        cell('num w-half', 'Груз').textContent = kgText(stop.kg) + (stop.share > 1 ? ' (1/' + stop.share + ')' : '');
        cell('num w-half', 'Сумма').textContent = money(stop.revenue / (stop.share || 1));
        cell('w-half', 'Менеджер').textContent = stop.agent_name || stop.agent_code || '—';
        const act = cell('dp-acts', '');
        act.appendChild(moveSelect(stop, tripId, tripId === null ? 'в рейс' : 'в другой рейс'));
        const ex = document.createElement('button');
        ex.type = 'button';
        ex.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        ex.textContent = 'Не везём';
        ex.setAttribute('aria-label', 'Не везём сегодня: ' + (stop.name || stop.code));
        ex.addEventListener('click', () => excludeStop(stop));
        act.appendChild(ex);
        return tr;
    }
    function stopsTable(stops, tripId) {
        const wrap = document.createElement('div');
        wrap.className = 'rt-table-scroll';
        const t = document.createElement('table');
        t.className = 'rt-table dp-stops';
        t.innerHTML = '<thead><tr><th scope="col">№</th><th scope="col">Магазин и адрес</th><th scope="col">Груз</th><th scope="col">Сумма</th><th scope="col">Менеджер</th><th scope="col"><span class="rt-sr-only">Действия</span></th></tr></thead><tbody></tbody>';
        const tb = t.querySelector('tbody');
        stops.forEach((s, i) => tb.appendChild(stopRow(s, i + 1, tripId)));
        wrap.appendChild(t);
        return wrap;
    }

    function renderUnassigned(plan) {
        const box = $('dpUnassigned');
        box.textContent = '';
        if (!plan.unassigned.length) return;
        const card = document.createElement('section');
        card.className = 'rt-card dp-unassigned';
        card.innerHTML = '<div class="rt-card-head"><h3 class="rt-card-title"><i class="fas fa-inbox" aria-hidden="true"></i>Ещё не в рейсах</h3><span class="rt-card-state is-todo"></span></div><p class="rt-card-lead">Новые заказы, вернувшиеся заказы или магазины с новой точкой. Перенесите каждый в рейс или нажмите «Собрать рейсы заново».</p>';
        card.querySelector('.rt-card-state').textContent = pl(plan.unassigned.length, 'магазин', 'магазина', 'магазинов');
        card.appendChild(stopsTable(plan.unassigned, null));
        box.appendChild(card);
    }

    function renderTruckCards(plan) {
        const box = $('dpTruckCards');
        box.textContent = '';
        plan.trucks.forEach(t => {
            const card = document.createElement('section');
            card.className = 'rt-card dp-tcard';
            card.style.setProperty('--dp-c', truckColor(t.car_code));
            const head = document.createElement('div');
            head.className = 'dp-thead';
            const h = document.createElement('h3');
            h.className = 'rt-card-title';
            h.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span></span>';
            h.querySelector('.rt-dot').style.background = truckColor(t.car_code);
            h.lastChild.textContent = truckLabel(t);
            const st = document.createElement('p');
            st.className = 'dp-tstats';
            st.textContent = pl(t.trips.length, 'рейс', 'рейса', 'рейсов') + ' · ' + pl(t.stops, 'точка', 'точки', 'точек') + ' · ' + kgText(t.kg)
                + ' · ' + fmt(t.km, 1) + NB + 'км · ' + fmt(t.liters, 1) + NB + 'л · вернётся в ' + t.return;
            if (t.over_time) st.classList.add('is-bad');
            head.append(h, st);
            card.appendChild(head);
            t.trips.forEach((tr, i) => card.appendChild(tripBlock(t, tr, i)));
            box.appendChild(card);
        });
    }

    function tripBlock(t, tr, i) {
        const div = document.createElement('div');
        div.className = 'dp-trip' + (tr.over_time || tr.over_capacity ? ' is-bad' : '');
        const head = document.createElement('div');
        head.className = 'dp-trip-head';
        const title = document.createElement('h4');
        title.className = 'dp-trip-t';
        title.textContent = 'Рейс ' + (i + 1) + ': выезд ' + tr.depart + ' → возвращение ' + tr.return;
        const meta = document.createElement('span');
        meta.className = 'dp-trip-meta';
        meta.textContent = kgText(tr.kg) + (tr.load_pct !== null ? ' (' + tr.load_pct + '% машины)' : '') + ' · ' + fmt(tr.km, 1) + NB + 'км · ' + fmt(tr.liters, 1) + NB + 'л';
        const flags = document.createElement('span');
        flags.className = 'dp-trip-flags';
        if (tr.over_time) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">не успевает до ' + esc(state.data.work_end) + '</span>');
        if (tr.over_capacity) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-danger">перегруз</span>');
        if (tr.pinned) flags.insertAdjacentHTML('beforeend', '<span class="rt-badge b-ok"><i class="fas fa-lock" aria-hidden="true"></i>закреплён</span>');
        const tools = document.createElement('div');
        tools.className = 'dp-trip-tools';
        const lab = document.createElement('label');
        lab.className = 'dp-trip-truck';
        lab.textContent = 'Машина ';
        const sel = document.createElement('select');
        sel.className = 'rt-select';
        sel.setAttribute('aria-label', 'Машина рейса ' + (i + 1) + ' (' + truckLabel(t) + ') — выбор закрепит её за рейсом');
        state.data.trucks.filter(x => x.selected).forEach(x => sel.add(new Option(truckLabel(x), x.car_code, false, x.car_code === t.car_code)));
        sel.addEventListener('change', () => edit({ action: 'pin', trip: tr.id, truck: sel.value }, 'Рейс закреплён за ' + truckLabel(truckBy(sel.value))));
        lab.appendChild(sel);
        const pin = document.createElement('button');
        pin.type = 'button';
        pin.className = 'rt-btn rt-btn-ghost rt-btn-sm';
        pin.setAttribute('aria-pressed', String(!!tr.pinned));
        pin.innerHTML = '<i class="fas ' + (tr.pinned ? 'fa-lock-open' : 'fa-lock') + '" aria-hidden="true"></i><span></span>';
        pin.lastChild.textContent = tr.pinned ? 'Открепить' : 'Закрепить';
        pin.title = tr.pinned ? 'При новой сборке рейс тоже переложится' : 'При новой сборке рейс останется как есть, на этой машине';
        pin.addEventListener('click', () => edit(tr.pinned ? { action: 'unpin', trip: tr.id } : { action: 'pin', trip: tr.id, truck: t.car_code },
            tr.pinned ? 'Рейс откреплён' : 'Рейс закреплён за ' + truckLabel(t)));
        tools.append(lab, pin);
        head.append(title, meta, flags, tools);
        div.appendChild(head);
        div.appendChild(stopsTable(tr.stops, tr.id));
        return div;
    }

    function renderBaseline(plan) {
        const b = plan.baseline;
        $('dpBaseline').hidden = !b;
        if (!b) return;
        $('dpBaselineNote').textContent = '≈ ' + fmt(b.km) + NB + 'км, ' + fmt(b.liters) + NB + 'л, ' + pl(b.trips, 'рейс', 'рейса', 'рейсов');
        const tb = $('dpBaselineRows');
        tb.textContent = '';
        b.trucks.forEach(r => {
            const tr = document.createElement('tr');
            [truckLabel(truckBy(r.car_code)) + (r.over_time ? ' — не успевает за день' : ''), fmt(r.stops), fmt(r.kg), fmt(r.trips), fmt(r.km, 1)].forEach((v, i) => {
                const td = document.createElement('td');
                td.textContent = v;
                td.setAttribute('data-label', ['Машина', 'Точек', 'кг', 'Рейсов', 'км'][i]);
                if (i) td.className = 'num w-half';
                tr.appendChild(td);
            });
            tb.appendChild(tr);
        });
    }

    // ---------- Карта ----------
    function ensureMap() {
        if (state.map || state.mapFailed) return;
        const el = $('dpMap');
        if (typeof window.L === 'undefined') {
            state.mapFailed = true;
            el.classList.add('rt-map-fallback');
            el.textContent = 'Карта не загрузилась (нет доступа к cdn.jsdelivr.net). Рейсы, печать и Excel работают.';
            return;
        }
        const map = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false });
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            subdomains: 'abc', maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
        }).addTo(map);
        map.setView(YEREVAN, 10);
        map.on('click focus', () => map.scrollWheelZoom.enable());
        map.on('mouseout blur', () => map.scrollWheelZoom.disable());
        state.map = map;
        state.layers = L.layerGroup().addTo(map);
    }
    function drawMap() {
        ensureMap();
        if (!state.map) return;
        const d = state.data, plan = d.plan;
        state.layers.clearLayers();
        state.map.invalidateSize();
        const bounds = [];
        const depot = d.depot ? [d.depot.lat, d.depot.lon] : null;
        const legend = $('dpLegend');
        legend.textContent = '';
        plan.trucks.forEach(t => {
            const color = truckColor(t.car_code);
            const lg = document.createElement('span');
            lg.className = 'dp-lg';
            lg.innerHTML = '<span class="rt-dot" aria-hidden="true"></span><span></span>';
            lg.firstChild.style.background = color;
            lg.lastChild.textContent = truckLabel(t) + ' — ' + pl(t.trips.length, 'рейс', 'рейса', 'рейсов');
            legend.appendChild(lg);
            t.trips.forEach((tr, ti) => {
                const pts = tr.stops.filter(s => s.lat !== null).map(s => [s.lat, s.lon]);
                const line = depot ? [depot, ...pts, depot] : pts;
                if (line.length > 1) L.polyline(line, { color, weight: 3, opacity: .85, dashArray: ti % 2 ? '6 6' : null }).addTo(state.layers);
                tr.stops.forEach((s, si) => {
                    if (s.lat === null) return;
                    bounds.push([s.lat, s.lon]);
                    const m = L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: color, fillOpacity: 1 });
                    m.bindTooltip(esc(truckLabel(t)) + ' · рейс ' + (ti + 1) + ' · №' + (si + 1) + '<br>' + esc(s.name || s.code));
                    m.bindPopup('<div class="rt-pop"><b>' + esc(s.name || s.code) + '</b><br>' + esc(s.address || '') + '<br>'
                        + esc(kgText(s.kg)) + ' · ' + esc(money(s.revenue)) + '<br>' + esc(truckLabel(t)) + ', рейс ' + (ti + 1) + ', точка ' + (si + 1) + '</div>');
                    m.addTo(state.layers);
                });
            });
        });
        plan.unassigned.forEach(s => {
            if (s.lat === null) return;
            bounds.push([s.lat, s.lon]);
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#ffb547', weight: 3, fillColor: '#0c0f14', fillOpacity: 1 })
                .bindTooltip('Ещё не в рейсе: ' + esc(s.name || s.code)).addTo(state.layers);
        });
        if (depot) {
            bounds.push(depot);
            L.marker(depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<i class="fas fa-warehouse"></i>', iconSize: [28, 28] }), keyboard: false })
                .bindTooltip('Склад').addTo(state.layers);
        }
        if (plan.unassigned.length) {
            const lg = document.createElement('span');
            lg.className = 'dp-lg';
            lg.innerHTML = '<span class="rt-dot dp-dot-open" aria-hidden="true"></span><span>ещё не в рейсе</span>';
            legend.appendChild(lg);
        }
        if (bounds.length) state.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 });
    }

    // ---------- Действия ----------
    function setBusy(on) {
        state.busy = on;
        document.querySelectorAll('#dpBody button, #dpBody select').forEach(x => {
            if (on) { x.dataset.wasDisabled = x.disabled ? '1' : ''; x.disabled = true; } else if (x.dataset.wasDisabled !== undefined) { x.disabled = x.dataset.wasDisabled === '1'; delete x.dataset.wasDisabled; }
        });
        $('rtDispatch').setAttribute('aria-busy', String(on));
    }

    async function build() {
        if (state.busy) return;
        const trucks = selectedTrucks();
        if (!trucks.length) { showActionError(new Error('Отметьте хотя бы одну машину в шаге 1.')); return; }
        hideActionError();
        setBusy(true);
        $('dpBuildText').textContent = 'Собираю рейсы…';
        try {
            const data = await api('POST', '/api/routes/dispatch/build', { date: state.day, trucks });
            setBusy(false);
            setData(data);
            toast('Рейсы собраны: ' + pl(data.plan.summary.trips, 'рейс', 'рейса', 'рейсов') + ', ≈ ' + fmt(data.plan.summary.km) + NB + 'км');
            $('dpStep3Title').focus();
        } catch (e) {
            setBusy(false);
            render();
            showActionError(e);
        }
    }

    async function edit(body, okText) {
        if (state.busy) return null;
        hideActionError();
        setBusy(true);
        try {
            const data = await api('POST', '/api/routes/dispatch/edit', { date: state.day, rev: state.data.rev, ...body });
            setBusy(false);
            setData(data);
            if (okText) toast(okText + (data.delta_km !== undefined && data.delta_km !== null ? ' — ' + deltaText(data.delta_km) : '') + '.');
            return data;
        } catch (e) {
            setBusy(false);
            render();
            showActionError(e);
            return null;
        }
    }

    async function excludeStop(stop) {
        if (!needPlan()) return;
        const orders = stop.orders || [];
        let ok = true;
        for (let i = 0; i < orders.length && ok; i++) {
            ok = !!(await edit({ action: 'exclude', order: orders[i].isn }, i === orders.length - 1 ? '«' + (stop.name || stop.code) + '» не везём сегодня' : null));
        }
    }

    async function reset() {
        if (state.busy) return;
        if (!window.confirm('Удалить рейсы этого дня вместе с правками (переносы, закрепления, «не везём»)?')) return;
        setBusy(true);
        try {
            const data = await api('POST', '/api/routes/dispatch/reset', { date: state.day });
            setBusy(false);
            setData(data);
            toast('План дня удалён — можно собрать рейсы заново.');
        } catch (e) { setBusy(false); render(); showActionError(e); }
    }

    // ---------- Печать и Excel ----------
    function printSheets() {
        const d = state.data, plan = d.plan;
        if (!plan) return;
        const dayText = (WD_ACC[d.weekday] || '') + ', ' + dateRu(d.day);
        let html = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Развоз на ' + esc(dateRu(d.day)) + '</title><style>'
            + 'body{font-family:Arial,Helvetica,sans-serif;color:#000;margin:0;padding:12mm;font-size:15px}'
            + '.sheet{page-break-after:always;break-after:page}.sheet:last-child{page-break-after:auto;break-after:auto}'
            + 'h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:18px 0 6px}.sub{font-size:15px;margin:0 0 10px}'
            + 'table{width:100%;border-collapse:collapse}th,td{border:1px solid #000;padding:7px 8px;vertical-align:top;text-align:left}'
            + 'th{font-size:13px;background:#eee}td.n{font-size:22px;font-weight:700;width:38px;text-align:center}td.kg{font-size:18px;font-weight:700;white-space:nowrap;width:90px}'
            + 'td.ok{width:60px}.addr{font-size:17px}.nm{font-weight:700}@media screen{body{background:#fff}}'
            + '</style></head><body>';
        plan.trucks.forEach(t => {
            html += '<section class="sheet"><h1>' + esc(truckLabel(t)) + '</h1><p class="sub">Развоз на ' + esc(dayText) + ' · '
                + esc(pl(t.trips.length, 'рейс', 'рейса', 'рейсов')) + ' · ' + esc(pl(t.stops, 'точка', 'точки', 'точек')) + ' · ' + esc(kgText(t.kg))
                + ' · ≈ ' + esc(fmt(t.km)) + ' км</p>';
            t.trips.forEach((tr, i) => {
                html += '<h2>Рейс ' + (i + 1) + ': выезд ' + esc(tr.depart) + ', ' + esc(kgText(tr.kg)) + ', ≈ ' + esc(fmt(tr.km)) + ' км</h2>'
                    + '<table><thead><tr><th>№</th><th>Магазин и адрес</th><th>Груз</th><th>Отметка</th></tr></thead><tbody>';
                tr.stops.forEach((s, si) => {
                    html += '<tr><td class="n">' + (si + 1) + '</td><td><div class="nm">' + esc(s.name || s.code) + ' <small>(' + esc(s.code) + ')</small></div>'
                        + '<div class="addr">' + esc(s.address || 'адреса в ERP нет') + '</div></td><td class="kg">' + esc(kgText(s.kg)) + '</td><td class="ok"></td></tr>';
                });
                html += '</tbody></table>';
            });
            html += '</section>';
        });
        html += '</body></html>';
        const w = window.open('', '_blank');
        if (!w) { showActionError(new Error('Браузер не дал открыть окно печати — разрешите всплывающие окна для этого сайта.')); return; }
        w.document.open();
        w.document.write(html);
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* окно закрыли раньше */ } }, 300);
    }

    function exportExcel() {
        const d = state.data, plan = d.plan;
        if (!plan) return;
        if (typeof window.XLSX === 'undefined') { showActionError(new Error('Библиотека Excel не загрузилась (нет доступа к cdn.jsdelivr.net).')); return; }
        const rows = [['Машина', 'Рейс', 'Выезд', 'Возвращение', '№', 'Код', 'Магазин', 'Адрес', 'Менеджер', 'Груз, кг', 'Сумма, драм', 'Широта', 'Долгота']];
        plan.trucks.forEach(t => t.trips.forEach((tr, i) => tr.stops.forEach((s, si) => rows.push([
            truckLabel(t), i + 1, tr.depart, tr.return, si + 1, s.code, s.name, s.address, s.agent_name || s.agent_code,
            s.kg, Math.round(s.revenue / (s.share || 1)), s.lat, s.lon]))));
        const sum = [['Машина', 'Рейсов', 'Точек', 'Груз, кг', 'км', 'Литры', 'Вернётся']];
        plan.trucks.forEach(t => sum.push([truckLabel(t), t.trips.length, t.stops, t.kg, t.km, t.liters, t.return]));
        sum.push(['Итого', plan.summary.trips, plan.summary.stops, plan.summary.kg, plan.summary.km, plan.summary.liters, '']);
        if (plan.baseline) sum.push(['Если по менеджерам', plan.baseline.trips, '', '', plan.baseline.km, plan.baseline.liters, '']);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), 'Рейсы');
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(sum), 'Машины');
        XLSX.writeFile(wb, 'razvoz_' + d.day + '.xlsx');
        announce('Файл Excel скачан');
    }

    // ---------- План и факт ----------
    async function loadFact() {
        const out = $('dpFactOut');
        out.innerHTML = '<p class="dp-note"><span class="rt-spin-inline" aria-hidden="true"></span> Считаю факт и план за ' + esc(dateRu(state.day)) + '…</p>';
        $('dpFactBtn').disabled = true;
        try {
            const r = (await api('GET', '/api/routes/dispatch/fact?date=' + encodeURIComponent(state.day))).fact;
            out.textContent = '';
            if (!r.stops) { out.innerHTML = '<p class="dp-note">В этот день машины с заполненным тоннажем ничего не везли.</p>'; return; }
            const f = r.fact, p = r.plan;
            const pct = f.km > 0 ? Math.round((f.km - p.km) / f.km * 100) : 0;
            const lead = document.createElement('p');
            lead.className = 'dp-fact-lead';
            lead.textContent = r.saved_km > 0
                ? 'По плану было бы на ' + fmt(r.saved_km) + NB + 'км меньше (' + pct + '%) и ≈ на ' + fmt(f.liters - p.liters) + NB + 'л дизеля меньше.'
                : 'Фактическая раскладка была не хуже плана: ' + fmt(f.km) + ' против ' + fmt(p.km) + NB + 'км.';
            out.appendChild(lead);
            const t = document.createElement('table');
            t.className = 'rt-table dp-small';
            t.innerHTML = '<thead><tr><th scope="col"></th><th scope="col">км</th><th scope="col">литры</th><th scope="col">рейсов</th><th scope="col">не успевают за день</th></tr></thead><tbody></tbody>';
            [['Как развезли (ERP)', f], ['По плану программы', p]].forEach(([nm, x]) => {
                const tr = document.createElement('tr');
                [nm, fmt(x.km), fmt(x.liters), fmt(x.trips), fmt(x.trips_over_time)].forEach((v, i) => {
                    const td = document.createElement('td');
                    td.textContent = v;
                    td.setAttribute('data-label', ['', 'км', 'литры', 'рейсов', 'не успевают за день'][i]);
                    if (i) td.className = 'num w-half'; else td.className = 'rt-cell-name';
                    tr.appendChild(td);
                });
                t.querySelector('tbody').appendChild(tr);
            });
            const wrap = document.createElement('div');
            wrap.className = 'rt-table-scroll';
            wrap.appendChild(t);
            out.appendChild(wrap);
            const note = document.createElement('p');
            note.className = 'dp-note';
            note.textContent = pl(r.stops, 'точка', 'точки', 'точек') + ', ' + kgText(r.kg) + ', машины: ' + r.trucks.map(c => truckLabel(truckBy(c))).join(', ') + '.'
                + (r.skipped.docs ? ' Не в сравнении: ' + pl(r.skipped.docs, 'документ', 'документа', 'документов') + ' (' + kgText(r.skipped.kg) + ') — без машины в ERP, машина без тоннажа в настройках или магазин без точки.' : '');
            out.appendChild(note);
        } catch (e) {
            out.textContent = '';
            const p = document.createElement('p');
            p.className = 'rt-ferr';
            p.textContent = e.message;
            out.appendChild(p);
        } finally { $('dpFactBtn').disabled = false; }
    }

    // ---------- Старт ----------
    function init() {
        const params = new URLSearchParams(window.location.search);
        const day = /^\d{4}-\d{2}-\d{2}$/.test(params.get('date') || '') ? params.get('date') : null;
        $('dpDate').addEventListener('change', () => { if (/^\d{4}-\d{2}-\d{2}$/.test($('dpDate').value)) load($('dpDate').value); });
        $('dpTomorrow').addEventListener('click', () => load(state.data ? state.data.default_day : null));
        $('dpRefresh').addEventListener('click', () => load(state.day, true));
        $('dpRetry').addEventListener('click', () => load(state.day || day));
        $('dpBuild').addEventListener('click', build);
        $('dpReset').addEventListener('click', reset);
        $('dpPrint').addEventListener('click', printSheets);
        $('dpExcel').addEventListener('click', exportExcel);
        $('dpFactBtn').addEventListener('click', loadFact);
        $('dpActionReload').addEventListener('click', () => { hideActionError(); load(state.day); });
        $('dpPickSave').addEventListener('click', savePick);
        $('dpPickCoord').addEventListener('input', () => {
            const p = parseCoord($('dpPickCoord').value);
            $('dpPickSave').disabled = !p;
            if (p) setPick(p[0], p[1]);
        });
        $('dpNoCoords').addEventListener('toggle', () => { if ($('dpNoCoords').open) ensurePickMap(); });
        $('dpMapBox').addEventListener('toggle', () => { if ($('dpMapBox').open && state.data && state.data.plan) drawMap(); });
        load(day);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

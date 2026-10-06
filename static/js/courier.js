/* «Առաքիչ» /courier — офис терминалов водителей (docs/plans/courier-app-plan.md §4).
   Вкладки: «Վարորդներ» (водители, PIN, машины и их терминалы: QR, «Նոր QR», смена машины), «Առաքում այսօր» (по
   машинам), «Մակնշում» (коды маркировки, CSV/Excel), «Կարգավորումներ» (маркируемые товары, тара, причины, APK).
   «Գումար» — отдельная страница /courier/money (courier_money.js); старая ссылка /courier#money ведёт туда.
   API: /api/courier/admin/* (только admin; POST — JSON). Всё, что пришло с сервера (имена, коды, магазины), выводится
   только через esc() или textContent. QR — SVG, построенный сервером (segno), вставляется как есть.
   Фото — только с /api/courier/admin/photos/<uuid> (id проверяется по шаблону uuid до вставки). */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const money = (v) => fmt(v, 2) + ' ֏';
    const today = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
    const timeOf = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(11, 16) : '—');
    const dateTime = (s) => (typeof s === 'string' && s.length >= 16 ? s.slice(8, 10) + '.' + s.slice(5, 7) + ' ' + s.slice(11, 16) : '—');

    // Վիճակը՝ նույն կանոնով, ինչ տերմինալում (պայմանագիր §5 կետ 12)
    const STATUS = { full: ['Ստացված է', 'b-ok'], partial: ['Մասնակի', 'b-warn'], refused: ['Հրաժարում', 'b-danger'], pending: ['Սպասում է', 'b-none'],
        in_progress: ['Ընթացքում', 'b-warn'], covered: ['Պատվերով արված է', 'b-ok'] };
    const COLLECT = { cash: 'Կանխիկ', cash_ecr: 'Կանխիկ ՀԴՄ', none: 'Չվերցնել', ask: 'Ճշտել' };
    const TYPE = { delivery: 'Առաքում', payment: 'Գումար', tare: 'Տարա', return: 'Վերադարձ', scan: 'Սկան', scan_cancel: 'Սկանի չեղարկում',
        unreadable: 'Կոդը չի կարդացվում', arrived: 'Ժամանում', day_closed: 'Օրվա ավարտ', geo_suggest: 'Կետի առաջարկ',
        track: 'GPS երթուղի', refuel: 'Լիցքավորում' };
    const FLAG = {
        foreign: 'Այլ մեքենայի կամ օրվա կետ', unknown_stop: 'Անհայտ կետ', duplicate_elsewhere: 'Կոդն արդեն տրվել է այլ տեղ',
        repeat: 'Կրկնակի սկան', scan_short: 'Մակնշման սկանը պակաս է', no_ecr_receipt: 'ՀԴՄ կտրոնի համարը չկա',
        paid_collect_none: 'Գումար է վերցվել, թեև պետք չէր', lines_incomplete: 'Ոչ բոլոր տողերն են նշված',
        unknown_line: 'Անհայտ տող', gtin_not_in_invoice: 'GTIN-ը այս ապրանքագրից չէ', group_no_pack: 'Տուփի քանակը սահմանված չէ',
        units_mismatch: 'Տուփի քանակը չի համընկնում', unknown_scan: 'Անհայտ սկան', no_photo: 'Լուսանկար չկա',
        qty_over_invoice: 'Քանակը ավելի է, քան ապրանքագրի վերջին տարբերակում', no_reason: 'Պատճառը նշված չէ',
        date_suspicious: 'Ամսաթիվը չի համընկնում ժամանակի հետ', no_payment: 'Վճարում չկա',
        collected_by_other: 'Վերցրել է այլ վարորդ', split_order: 'Մասնակի է՝ բաժանված պատվեր',
        merge_conflict: 'Ստուգել՝ պատվերով և ապրանքագրով նշումները չեն համընկնում',
        odometer_suspicious: 'Օդոմետրի ցուցմունքը կասկածելի է',
        helper_unconfirmed: 'Առաքիչը PIN-ով հաստատված չէ',
        car_by_time: 'Մեքենան որոշվել է տերմինալի ժամով (մեքենան փոխվել էր)՝ ստուգել',
    };
    const statusBadge = (s) => (s.status ? badge(...(STATUS[s.status] || [s.status, 'b-none'])) : '—') + (s.removed ? ' ' + badge('Հանված է', 'b-none') : '');
    const tareText = (t) => (t && t.length ? ' <span class="cr-muted">Տարա՝ ' + fmt(t.reduce((a, x) => a + (num(x.qty) || 0), 0), 2) + '</span>' : '');
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const thumbs = (photos) => (photos || []).filter(p => UUID.test(p.id)).map(p => {
        const url = '/api/courier/admin/photos/' + p.id;
        const alt = p.kind === 'signature' ? 'Ստորագրություն' : 'Լուսանկար';
        return '<a class="cr-thumb" href="' + url + '" target="_blank" rel="noopener" title="' + alt + '"><img src="' + url + '" alt="' + alt + '" loading="lazy" width="56" height="56"></a>';
    }).join('');
    const stopLabel = (f) => esc([f.customer, f.doc_number].filter(Boolean).join(' · ') || '—');
    const flagText = (f) => (f || []).map(x => FLAG[x] || x).join(', ');
    const badge = (text, cls) => '<span class="rt-badge ' + cls + '">' + esc(text) + '</span>';
    // Վարորդ և առաքիչ (պայմանագիր v1.4 §8)՝ առաքիչը վարորդի անվան տակ
    // «Ապրանքագիր» — карточка накладной (/courier/invoice): номер документа точки дня — ссылка
    const docLink = (date, stopId, text) => (date && stopId && text
        ? '<a class="cr-doc-link" href="/courier/invoice?date=' + encodeURIComponent(date) + '&stop=' + encodeURIComponent(stopId) + '">' + esc(text) + '</a>'
        : esc(text || ''));
    const whoCell = (driver, helper) => esc(driver || '') + (helper ? '<span class="cr-with"><i class="fas fa-user-group" aria-hidden="true"></i>Առաքիչ՝ ' + esc(helper) + '</span>' : '');

    // ---------- Сервер ----------
    function announce(text) { $('crStatus').textContent = text; }
    function showError(text) {
        const box = $('crError');
        if (!text) { box.classList.add('d-none'); return; }
        $('crErrorText').textContent = text;
        box.classList.remove('d-none');
    }
    async function api(url, opts = {}) {
        const init = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
        if (opts.json !== undefined) {
            init.method = 'POST';
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.json);
        } else if (opts.form) {
            init.method = 'POST';
            init.headers['X-Requested-With'] = 'fetch';
            init.body = opts.form;
        }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new Error('Սերվերը հասանելի չէ'); }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const msg = body && body.error ? body.error
                : resp.status === 401 ? 'Անհրաժեշտ է մուտք գործել' : resp.status === 403 ? 'Միայն ադմինիստրատորի համար' : 'Սերվերի սխալ (' + resp.status + ')';
            const err = new Error(msg);
            err.body = body;   // լրացուցիչ դաշտեր (օր.՝ unverifiable)
            throw err;
        }
        return body;
    }

    // ---------- Вкладки ----------
    const TABS = ['drivers', 'today', 'marks', 'settings'];
    const loaded = new Set();
    function showTab(name) {
        if (name === 'money') { location.replace('/courier/money'); return; }   // вкладка стала отдельной страницей
        if (!TABS.includes(name)) name = 'drivers';
        TABS.forEach(t => {
            $('crPane-' + t).hidden = t !== name;
            $('crTab-' + t).setAttribute('aria-selected', String(t === name));
        });
        showError('');
        if (!loaded.has(name)) { loaded.add(name); LOADERS[name](); }
    }

    // ---------- Водители и терминалы ----------
    const drv = { data: null };
    async function loadDrivers() {
        try {
            drv.data = await api('/api/courier/admin/drivers');
            renderDrivers();
        } catch (e) { showError(e.message); }
    }
    // Инициалы в кружке водителя: первые буквы двух первых слов (армянская буква — целым символом)
    const initials = (name) => String(name || '').trim().split(/\s+/).slice(0, 2).map(w => Array.from(w)[0] || '').join('').toUpperCase() || '?';
    // Плитка итога (.rt-kpi из routes.css); sub — готовый HTML, числа в нём уже через fmt()
    const kpi = (cls, icon, label, now, unit, sub) => '<div class="rt-kpi ' + cls + '"><div class="rt-kpi-label"><i class="fas ' + icon + '" aria-hidden="true"></i>'
        + esc(label) + '</div><div class="rt-kpi-val"><span class="now">' + esc(now) + '</span>' + (unit ? '<span class="unit">' + esc(unit) + '</span>' : '')
        + '</div><div class="rt-kpi-sub">' + sub + '</div></div>';
    const isLocked = (t) => !t.revoked_at && !!t.locked_until && new Date(t.locked_until) > new Date();
    function renderDrivers() {
        const d = drv.data;
        const working = d.drivers.filter(x => x.active);
        const noPin = working.filter(x => x.pin_reset || !x.has_pin);
        const live = d.terminals.filter(t => !t.revoked_at);
        const seenToday = live.filter(t => typeof t.last_seen_at === 'string' && t.last_seen_at.slice(0, 10) === today());
        const blocked = live.filter(isLocked);
        const issues = noPin.length + blocked.length;
        const bound = new Set(live.map(t => t.car_code));
        const fleetFree = d.cars.filter(c => c.fleet && !bound.has(c.code));
        $('crDrvKpis').innerHTML = kpi('', 'fa-id-card', 'Վարորդներ', fmt(working.length), '/ ' + fmt(d.drivers.length), 'աշխատում են')
            + kpi('', 'fa-mobile-screen', 'Տերմինալներ', fmt(live.length), '', 'այսօր կապի մեջ՝ <b>' + fmt(seenToday.length) + '</b>')
            + kpi(fleetFree.length ? 'is-warn' : 'is-good', 'fa-truck', 'Մեքենաներ առանց տերմինալի', fmt(fleetFree.length), '',
                'առաքման մեքենաներից՝ <b>' + fmt(d.cars.filter(c => c.fleet).length) + '</b>')
            + kpi(issues ? 'is-warn' : 'is-good', issues ? 'fa-triangle-exclamation' : 'fa-circle-check', 'Ուշադրություն', fmt(issues), '',
                issues ? [noPin.length ? 'PIN չունի՝ <b>' + fmt(noPin.length) + '</b>' : '', blocked.length ? 'արգելափակված՝ <b>' + fmt(blocked.length) + '</b>' : '']
                    .filter(Boolean).join(' · ') : 'Ամեն ինչ կարգին է');
        $('crDriversCount').textContent = d.drivers.length ? fmt(d.drivers.length) : '';
        $('crTermCount').textContent = live.length ? fmt(live.length) : '';
        const editing = $('crDriverId').value;
        // Работающие — сверху, дальше по имени
        const drivers = d.drivers.slice().sort((a, b) => (b.active - a.active) || String(a.name).localeCompare(String(b.name), 'hy'));
        $('crDriverRows').innerHTML = drivers.length ? drivers.map(x => '<li class="cr-row' + (x.active ? '' : ' is-off') + (String(x.id) === editing ? ' is-editing' : '') + '">'
            + '<span class="cr-avatar" aria-hidden="true">' + esc(initials(x.name)) + '</span>'
            + '<span class="cr-row-main"><span class="cr-row-name">' + esc(x.name) + '</span>'
            + '<span class="cr-row-sub"><span class="cr-state' + (x.active ? ' is-on' : '') + '">' + (x.active ? 'Աշխատում է' : 'Չի աշխատում') + '</span>'
            + (x.pin_reset ? '<span>հին PIN-ը այլևս չի ստուգվում</span>' : '') + '</span></span>'
            + '<span class="cr-row-badge">' + (x.pin_reset ? badge('PIN-ը սահմանել նորից', 'b-danger')
                : x.has_pin ? badge('PIN կա', 'b-ok') : badge('PIN չկա', 'b-warn')) + '</span>'
            + '<button type="button" class="rt-iconbtn cr-row-act" data-edit="' + x.id + '" title="Փոփոխել" aria-label="Փոփոխել՝ ' + esc(x.name) + '">'
            + '<i class="fas fa-pen" aria-hidden="true"></i></button></li>').join('')
            : '<li class="cr-empty"><i class="fas fa-user-plus" aria-hidden="true"></i>Վարորդներ դեռ չկան՝ ավելացրեք առաջինը ներքևում</li>';
        renderCars(d, live);
        const sel = $('crTermCar');
        const picked = sel.value;   // список пересобирается после каждого сохранения — выбор не теряем
        sel.innerHTML = '<option value="">— ընտրեք —</option>' + carOptions(d.cars, '');
        if (picked && d.cars.some(c => c.code === picked)) sel.value = picked;
        $('crTermCarHint').textContent = d.cars_erp_failed ? 'ERP-ն հասանելի չէ՝ մեքենաների ցուցակը չբեռնվեց։'
            : 'Բոլոր մեքենաները՝ ERP-ից և «Առաքում» բաժնից։ Վերևում՝ առաքման մեքենաները։';
    }
    // Машины списка (сервер: парк «Развоза» сверху, дальше по коду) — <option> по группам; except — текущая машина терминала
    function carOptions(cars, except) {
        const opt = (c) => '<option value="' + esc(c.code) + '">' + esc(c.code + (c.name ? ' · ' + c.name : '')
            + (c.docs ? ' (' + c.docs + ' ապրանքագիր)' : '')) + '</option>';
        const rest = cars.filter(c => c.code !== except);
        const fleet = rest.filter(c => c.fleet), other = rest.filter(c => !c.fleet);
        return (fleet.length ? '<optgroup label="Առաքման մեքենաներ">' + fleet.map(opt).join('') + '</optgroup>' : '')
            + (other.length ? '<optgroup label="Մյուս մեքենաները">' + other.map(opt).join('') + '</optgroup>' : '');
    }
    // «Մեքենաներ և տերմինալներ»: строка на машину — её действующие терминалы и действия; машины без терминала вне парка —
    // в свёрнутом «Մյուս մեքենաները»; машина терминала, которой нет в списке (ERP недоступна, машина удалена), — тоже строкой
    function renderCars(d, live) {
        const byCar = new Map();
        live.forEach(t => { if (!byCar.has(t.car_code)) byCar.set(t.car_code, []); byCar.get(t.car_code).push(t); });
        const known = new Set(d.cars.map(c => c.code));
        const cars = d.cars.concat([...byCar.keys()].filter(code => !known.has(code)).sort()
            .map(code => ({ code, name: '', docs: 0, fleet: false, closed: false, capacity_kg: null })));
        const main = cars.filter(c => c.fleet || byCar.has(c.code));
        const other = cars.filter(c => !c.fleet && !byCar.has(c.code));
        $('crCarRows').innerHTML = main.length ? main.map(c => carRow(c, byCar.get(c.code) || [], d.cars)).join('')
            : '<li class="cr-empty"><i class="fas fa-truck" aria-hidden="true"></i>' + (d.cars_erp_failed ? 'ERP-ն հասանելի չէ՝ մեքենաների ցուցակը չբեռնվեց' : 'Առաքման մեքենաներ չկան') + '</li>';
        $('crCarsOther').hidden = !other.length;
        $('crCarsOtherNote').textContent = other.length ? fmt(other.length) : '';
        $('crCarOtherRows').innerHTML = other.map(c => carRow(c, [], d.cars)).join('');
        const off = d.terminals.filter(t => t.revoked_at).sort((a, b) => String(b.revoked_at).localeCompare(String(a.revoked_at)));
        $('crTermOff').hidden = !off.length;
        $('crTermOffNote').textContent = off.length ? fmt(off.length) : '';
        $('crTermOffRows').innerHTML = off.map(t => '<li class="cr-row is-off">'
            + '<span class="cr-avatar is-device" aria-hidden="true"><i class="fas fa-mobile-screen"></i></span>'
            + '<span class="cr-row-main"><span class="cr-row-name">' + esc(t.name) + '</span>'
            + '<span class="cr-row-sub"><span class="cr-plate">' + esc(t.car_code) + '</span><span>անջատված՝ ' + esc(dateTime(t.revoked_at)) + '</span></span></span>'
            + '<span class="cr-row-badge">' + badge('Անջատված', 'b-none') + '</span></li>').join('');
    }
    function carRow(c, terms, cars) {
        const sub = [c.name ? '<span>' + esc(c.name) + '</span>' : '',
            num(c.capacity_kg) ? '<span>' + fmt(num(c.capacity_kg) / 1000, 1) + ' տ</span>' : '',
            c.docs ? '<span>' + fmt(c.docs) + ' ապրանքագիր 90 օրում</span>' : ''].join('');
        const badges = (c.fleet ? badge('Առաքման մեքենա', 'b-ok') : '') + (c.closed ? ' ' + badge('ERP-ում փակ', 'b-none') : '');
        return '<li class="cr-car' + (terms.length ? '' : ' is-free') + '">'
            + '<div class="cr-car-head"><span class="cr-avatar is-device" aria-hidden="true"><i class="fas fa-truck"></i></span>'
            + '<span class="cr-row-main"><span class="cr-row-name"><span class="cr-plate">' + esc(c.code) + '</span></span>'
            + (sub ? '<span class="cr-row-sub">' + sub + '</span>' : '') + '</span>'
            + '<span class="cr-row-badge">' + badges + '</span>'
            + (terms.length ? '' : '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm cr-row-act" data-attach="' + esc(c.code) + '">'
                + '<i class="fas fa-plus" aria-hidden="true"></i>Միացնել տերմինալ</button>')
            + '</div>'
            + (terms.length ? '<ul class="cr-terms" aria-label="Տերմինալներ՝ ' + esc(c.code) + '">' + terms.map(t => termRow(t, cars)).join('') + '</ul>' : '')
            + '</li>';
    }
    function termRow(t, cars) {
        const state = isLocked(t) ? badge('Արգելափակված PIN-ով', 'b-warn') : badge('Աշխատում է', 'b-ok');
        const seen = t.last_seen_at ? '<i class="far fa-clock" aria-hidden="true"></i>' + esc(dateTime(t.last_seen_at)) : 'դեռ չի միացել';
        const options = carOptions(cars, t.car_code);
        return '<li class="cr-term" data-term="' + t.id + '">'
            + '<span class="cr-term-main"><i class="fas fa-mobile-screen" aria-hidden="true"></i><span class="cr-row-name">' + esc(t.name) + '</span>'
            + state + '<span class="cr-seen" title="Վերջին կապը">' + seen + '</span></span>'
            + '<span class="cr-term-acts">'
            + '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-reissue="' + t.id + '" aria-label="Նոր QR՝ ' + esc(t.name) + '"><i class="fas fa-qrcode" aria-hidden="true"></i>Նոր QR</button>'
            + '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-move="' + t.id + '" aria-expanded="false" aria-controls="crMove' + t.id + '" aria-label="Փոխել մեքենան՝ ' + esc(t.name) + '">'
            + '<i class="fas fa-right-left" aria-hidden="true"></i>Փոխել մեքենան</button>'
            + '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm cr-danger" data-revoke="' + t.id + '" aria-label="Անջատել՝ ' + esc(t.name) + '">Անջատել</button>'
            + '</span>'
            + '<form class="cr-move" id="crMove' + t.id + '" data-move-form="' + t.id + '" hidden autocomplete="off">'
            + '<label for="crMoveCar' + t.id + '">Նոր մեքենա</label>'
            + (options ? '<select id="crMoveCar' + t.id + '" class="rt-select" required><option value="">— ընտրեք —</option>' + options + '</select>'
                + '<button type="submit" class="rt-btn rt-btn-primary rt-btn-sm"><i class="fas fa-check" aria-hidden="true"></i>Փոխել</button>'
                : '<span class="cr-muted">Մեքենաների ցուցակը չբեռնվեց (ERP)</span>')
            + '<button type="button" class="rt-btn rt-btn-ghost rt-btn-sm" data-move-cancel="' + t.id + '">Չեղարկել</button>'
            + '<span class="cr-move-note">Նախորդ օրերի առաքումներն ու գումարը մնում են նախկին մեքենայի վրա։ Վարորդը պետք է նորից մտնի PIN-ով։</span>'
            + '<span class="rt-ferr" role="alert"></span></form></li>';
    }
    function markEditing(id) {
        document.querySelectorAll('#crDriverRows .cr-row').forEach(li => {
            const b = li.querySelector('[data-edit]');
            li.classList.toggle('is-editing', id !== null && !!b && b.dataset.edit === String(id));
        });
        $('crDriverForm').classList.toggle('is-editing', id !== null);
    }
    function editDriver(id) {
        const x = drv.data.drivers.find(v => v.id === id);
        if (!x) return;
        $('crDriverId').value = String(x.id);
        $('crDriverName').value = x.name;
        $('crDriverPin').value = '';
        $('crDriverActive').checked = x.active;
        $('crDriverFormTitle').textContent = 'Փոփոխել՝ ' + x.name;
        $('crDriverPinHint').textContent = x.has_pin ? '(դատարկ՝ չփոխել)' : '(4–6 թվանշան)';
        $('crDriverNew').hidden = false;
        $('crDriverErr').textContent = '';
        markEditing(x.id);
        $('crDriverForm').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        $('crDriverName').focus({ preventScroll: true });
    }
    function resetDriverForm() {
        $('crDriverForm').reset();
        $('crDriverId').value = '';
        $('crDriverFormTitle').textContent = 'Նոր վարորդ';
        $('crDriverPinHint').textContent = '(4–6 թվանշան)';
        $('crDriverNew').hidden = true;
        $('crDriverErr').textContent = '';
        markEditing(null);
    }
    async function saveDriver(ev, reset) {
        if (ev) ev.preventDefault();
        const id = $('crDriverId').value ? Number($('crDriverId').value) : null;
        const pin = $('crDriverPin').value.trim();
        if (pin && !/^\d{4,6}$/.test(pin)) { $('crDriverErr').textContent = 'PIN-ը 4–6 թվանշան է'; return; }
        if (id === null && !pin) { $('crDriverErr').textContent = 'Նոր վարորդի համար գրեք PIN'; return; }
        try {
            await api('/api/courier/admin/drivers', { json: { id, name: $('crDriverName').value, pin: pin || null, active: $('crDriverActive').checked,
                reset_unverifiable: reset === true } });
            resetDriverForm();
            announce('Վարորդը պահպանված է');
            await loadDrivers();
        } catch (e) {
            // PIN-ի կրկնությունը չի ստուգվում (COURIER_PIN_PEPPER-ը չկա)՝ կամ վերականգնել այն, կամ բացահայտ զրոյացնել այդ վարորդների PIN-ը
            const lost = e.body && e.body.unverifiable;
            if (reset !== true && lost && lost.length && window.confirm(e.message + '\n\nԿամ զրոյացրեք այս վարորդների PIN-ը՝ '
                    + lost.map(x => x.name).join(', ') + '։ Նրանք կկարողանան մտնել միայն նոր PIN-ով, որը կսահմանեք այստեղ։ Զրոյացնե՞լ։')) {
                return saveDriver(null, true);
            }
            $('crDriverErr').textContent = e.message;
        }
    }
    // QR регистрации (новый терминал и «Նոր QR»): reissued — прежний QR этого терминала уже не работает
    function showQr(r, reissued) {
        $('crQr').innerHTML = r.qr_svg || '<p style="color:#000;padding:8px">QR-ը չստեղծվեց (segno գրադարանը չկա) — օգտագործեք տեքստը ներքևում</p>';
        // Старый сервер может прислать SVG без viewBox: CSS иначе обрезает сам рисунок.
        const svg = $('crQr').querySelector('svg');
        if (svg && !svg.hasAttribute('viewBox')) {
            const width = svg.width.baseVal.value, height = svg.height.baseVal.value;
            if (width > 0 && height > 0) svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        }
        $('crQrFor').textContent = r.terminal ? r.terminal.name + ' · ' + r.terminal.car_code : '';
        $('crQrOld').hidden = !reissued;
        $('crQrText').textContent = r.qr_text;
        $('crQrAdminPin').textContent = r.admin_pin || '—';
        $('crQrBox').hidden = false;
        $('crQrBox').scrollIntoView({ block: 'center', behavior: 'smooth' });
        $('crQrBox').focus({ preventScroll: true });
    }
    async function createTerminal(ev) {
        ev.preventDefault();
        $('crTermErr').textContent = '';
        try {
            const r = await api('/api/courier/admin/terminals', { json: { name: $('crTermName').value, car_code: $('crTermCar').value, url: $('crTermUrl').value } });
            showQr(r, false);
            $('crTermForm').reset();
            announce('Տերմինալը ստեղծված է — սկանավորեք QR-ը');
            await loadDrivers();
        } catch (e) { $('crTermErr').textContent = e.message; }
    }
    // «Միացնել տերմինալ» у машины без терминала — форма нового терминала с этой машиной
    function attachTerminal(code) {
        const sel = $('crTermCar');
        if ([...sel.options].some(o => o.value === code)) sel.value = code;
        $('crTermErr').textContent = '';
        $('crTermForm').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        $('crTermName').focus({ preventScroll: true });
    }
    async function reissueTerminal(id) {
        const t = drv.data.terminals.find(v => v.id === id);
        if (!t || !window.confirm('Նոր QR «' + t.name + '» տերմինալի համար (' + t.car_code + ')։\n\nՀին QR-ը անմիջապես կդադարի աշխատել, '
            + 'վարորդը պետք է նորից մտնի PIN-ով։ Տերմինալի պատմությունը մնում է։\n\nԵթե սարքը փոխարինում եք նորով, հին սարքում '
            + 'չուղարկված տվյալները (առաքումներ, գումար) կկորեն։ Շարունակե՞լ։')) return;
        try {
            // адрес в QR — как выбран в форме нового терминала («Որտեղից է միանալու»; по умолчанию — интернет)
            const r = await api('/api/courier/admin/terminals/' + id + '/reissue', { json: { url: $('crTermUrl').value } });
            showQr(r, true);
            announce('Նոր QR-ը պատրաստ է — հինն այլևս չի աշխատում');
            await loadDrivers();
        } catch (e) { showError(e.message); }
    }
    function toggleMove(id, open) {
        const f = document.querySelector('[data-move-form="' + id + '"]');
        const b = document.querySelector('[data-move="' + id + '"]');
        if (!f) return;
        f.hidden = !open;
        if (b) b.setAttribute('aria-expanded', String(open));
        if (open) { const s = f.querySelector('select'); if (s) s.focus(); }
    }
    async function moveTerminal(ev) {
        const f = ev.target.closest('[data-move-form]');
        if (!f) return;
        ev.preventDefault();
        const id = Number(f.dataset.moveForm);
        const car = f.querySelector('select').value;
        const err = f.querySelector('.rt-ferr');
        err.textContent = '';
        if (!car) { err.textContent = 'Ընտրեք մեքենան'; return; }
        try {
            await api('/api/courier/admin/terminals/' + id + '/car', { json: { car_code: car } });
            announce('Տերմինալը տեղափոխված է ' + car + ' մեքենայի վրա');
            await loadDrivers();
        } catch (e) { err.textContent = e.message; }
    }
    async function revokeTerminal(id) {
        const t = drv.data.terminals.find(v => v.id === id);
        if (!t || !window.confirm('Անջատե՞լ «' + t.name + '» տերմինալը։ Այն այլևս չի կարողանա միանալ, պետք կլինի նոր QR։')) return;
        try {
            await api('/api/courier/admin/terminals/' + id + '/revoke', { json: {} });
            announce('Տերմինալն անջատված է');
            await loadDrivers();
        } catch (e) { showError(e.message); }
    }

    // ---------- Доставки сегодня ----------
    async function loadToday() {
        const box = $('crTodayCars');
        box.innerHTML = '<div class="rt-loading"><div class="rt-spinner" aria-hidden="true"></div>Բեռնում եմ…</div>';
        try {
            const d = await api('/api/courier/admin/today?date=' + encodeURIComponent($('crTodayDate').value || today()));
            renderToday(d);
        } catch (e) { box.innerHTML = ''; showError(e.message); }
    }
    function renderToday(d) {
        const mm = d.mismatch || {};
        const short = (mm.coverage || []).filter(c => c.terminal < c.plan);
        $('crMismatch').innerHTML = (short.length
            ? '<div class="rt-alert is-warn"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="rt-alert-text"><b>Պլանի ոչ բոլոր խանութներն են հասել տերմինալին</b><ul>'
              + short.map(c => '<li>' + esc(c.car_code) + '՝ պլանում ' + fmt(c.plan) + ', տերմինալին հասել է ' + fmt(c.terminal) + '</li>').join('') + '</ul></span></div>'
            : '')
            + ((mm.items && mm.items.length)
            ? '<div class="rt-alert is-warn"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i><span class="rt-alert-text"><b>Ապրանքագրի մեքենան ERP-ում չի համապատասխանում «Առաքում» պլանին — ստուգեք ERP-ում</b><ul>'
              + mm.items.map(x => '<li>' + esc(x.customer_name || x.customer_code) + ' · ' + esc(x.doc_number) + ' — ERP՝ ' + esc(x.erp_car || 'առանց մեքենայի')
              + ', պլան՝ ' + esc(x.plan_cars.join(', ')) + '</li>').join('') + '</ul></span></div>'
            : (mm.error ? '<p class="cr-lead">Համեմատել պլանի հետ չհաջողվեց՝ ' + esc(mm.error) + '</p>' : ''))
            + (mm.plan_exists && mm.released === false ? '<p class="cr-lead">«Առաքում» պլանը դեռ հաստատված չէ․ տերմինալները պլանի խանութները կստանան հաստատելուց հետո (ERP-ում մեքենայով ապրանքագրերը՝ հիմա)։</p>' : '')
            + (mm.no_car && mm.released !== false ? '<p class="cr-lead">Պլանի խանութների ' + fmt(mm.no_car) + ' ապրանքագիր ERP-ում առանց մեքենայի է։ Մեքենան որոշվում է ըստ պլանի։</p>' : '')
            + crewMismatch(d.crew_mismatch);
        const plan = d.crew_mismatch && d.crew_mismatch.available ? (d.crew_mismatch.planned || {}) : null;
        $('crTodayCars').innerHTML = d.cars.length ? d.cars.map(c => carCard(c, plan, d.date)).join('')
            : '<p class="rt-empty">Այս օրվա համար տվյալներ չկան։ Տերմինալ ունեցող մեքենաների կետերը կերևան այստեղ։</p>';
        $('crFlaggedBox').hidden = !d.flagged.length;
        $('crFlaggedNote').textContent = d.flagged.length ? String(d.flagged.length) : '';
        $('crFlaggedRows').innerHTML = d.flagged.map(f => '<tr><td>' + esc(timeOf(f.at)) + '</td><td>' + esc(f.car_code) + '</td><td>' + whoCell(f.driver_name, f.helper_name)
            + '</td><td>' + stopLabel(f) + '</td><td>' + esc(TYPE[f.type] || f.type) + '</td><td>' + esc(flagText(f.flags)) + '</td><td>' + thumbs(f.photos) + '</td></tr>').join('');
        const ph = d.photo_events || [];
        $('crPhotosBox').hidden = !ph.length;
        $('crPhotosNote').textContent = ph.length ? String(ph.length) : '';
        $('crPhotosRows').innerHTML = ph.map(f => '<tr><td>' + esc(timeOf(f.at)) + '</td><td>' + esc(f.car_code) + '</td><td>' + whoCell(f.driver_name, f.helper_name)
            + '</td><td>' + stopLabel(f) + '</td><td>' + esc(TYPE[f.type] || f.type) + '</td><td>' + thumbs(f.photos) + '</td></tr>').join('');
        $('crRejectedBox').hidden = !d.rejected.length;
        $('crRejectedNote').textContent = d.rejected.length ? String(d.rejected.length) : '';
        $('crRejectedRows').innerHTML = d.rejected.map(r => '<li><span class="grow">' + esc(r.driver_name || '') + (r.helper_name ? ' · Առաքիչ՝ ' + esc(r.helper_name) : '') + ' · ' + esc(TYPE[r.type] || r.type || '')
            + ' — ' + esc(r.message) + '</span><span class="cr-muted">' + esc(dateTime(r.received_at)) + '</span></li>').join('');
    }
    // Լիցքավորումներ (պայմանագիր §7)՝ ուղղվածը (supersedes) մոխրագույն, կասկածելի օդոմետրը՝ նշումով
    function refuelsBlock(list) {
        if (!list.length) return '';
        return '<details class="rt-fold" style="margin-top:10px"><summary><span class="rt-fold-t"><i class="fas fa-gas-pump" aria-hidden="true"></i>Լիցքավորումներ</span><span class="rt-fold-note">' + fmt(list.length) + '</span></summary>'
            + '<div class="rt-fold-body"><div class="rt-table-scroll"><table class="rt-table cr-small"><thead><tr><th scope="col">Ժամ</th><th scope="col">Վարորդ</th><th scope="col">Լիտր</th><th scope="col">Օդոմետր, կմ</th><th scope="col">Լրիվ բաք</th><th scope="col">Գումար</th><th scope="col">Նշում</th><th scope="col">Լուսանկար</th></tr></thead><tbody>'
            + list.map(r => '<tr class="' + (r.superseded ? 'is-closed' : '') + '"><td>' + esc(timeOf(r.at)) + '</td><td>' + esc(r.driver_name || '') + '</td><td class="cr-num-cell">' + fmt(r.liters, 2)
                + '</td><td class="cr-num-cell">' + fmt(r.odometer_km) + '</td><td>' + (r.full_tank ? 'Այո' : 'Ոչ') + '</td><td class="cr-num-cell">' + (r.amount_amd === null || r.amount_amd === undefined ? '—' : money(r.amount_amd))
                + '</td><td>' + esc([r.superseded ? 'Ուղղված է' : '', flagText(r.flags)].filter(Boolean).join(', ')) + '</td><td>' + thumbs(r.photos) + '</td></tr>').join('')
            + '</tbody></table></div></div></details>';
    }
    // «Պլան ≠ փաստ» անձնակազմով (պայմանագիր v1.4 §8)՝ «Առաքում» պլանի վարորդը/առաքիչը և տերմինալում փաստացին
    function crewMismatch(cm) {
        const items = (cm && cm.items) || [];
        if (!items.length) return '';
        return '<div class="rt-alert is-warn"><i class="fas fa-user-group" aria-hidden="true"></i><span class="rt-alert-text"><b>Անձնակազմը չի համընկնում «Առաքում» պլանի հետ</b><ul>'
            + items.map(x => '<li>' + esc(x.car_code) + ' · Ըստ պլանի ' + (x.role === 'helper' ? 'առաքիչ' : 'վարորդ') + '՝ ' + esc(x.planned)
                + ', փաստացի՝ ' + (x.fact && x.fact.length ? esc(x.fact.join(', ')) : 'մենակ') + '</li>').join('')
            + '</ul></span></div>';
    }
    // Մեքենայի անձնակազմը՝ «Վարորդ՝ A · Առաքիչ՝ B» կամ «մենակ»։ Հին տերմինալը անձնակազմ չի հաղորդում՝ միայն վարորդը։
    // Անձնակազմ՝ վարորդ և յուրաքանչյուր առաքիչ առանձին՝ առաջին հաստատման հերթականությամբ, իր նշումներով.
    // «ԺԺ:ՐՐ-ից» (օրվա ոչ առաջինը), «մինչև ԺԺ:ՐՐ» (գրասենյակը հանել է՝ մոխրագույն), «պլանում չկա» (միայն նրա մոտ, ով չկա պլանում)
    const crewName = (n) => String(n || '').trim().split(/\s+/).join(' ').toLocaleLowerCase('hy');
    function crewStrip(c, plan) {
        const person = (role, name, cls, notes) => '<span class="cr-crew-p ' + cls + '"><span class="cr-avatar" aria-hidden="true">' + esc(initials(name)) + '</span>'
            + '<span class="cr-crew-t"><small>' + role + '</small><b>' + esc(name) + '</b>'
            + (notes.length ? '<span class="cr-crew-note">' + notes.map(esc).join(' · ') + '</span>' : '') + '</span></span>';
        const drivers = c.drivers || [];
        const info = c.helper_info || (c.helpers || []).map(n => ({ name: n, since: null, until: null }));
        const planned = plan ? crewName((plan[c.car_code] || {}).helper) : null;
        const helpers = info.map((h, i) => person('Առաքիչ՝', h.name, h.until ? 'is-helper is-revoked' : 'is-helper', [
            i > 0 && h.since ? timeOf(h.since) + '-ից' : '',
            h.until ? 'մինչև ' + timeOf(h.until) : '',
            planned !== null && crewName(h.name) !== planned ? 'պլանում չկա' : '',
        ].filter(Boolean)));
        if (!helpers.length && c.alone) {
            helpers.push('<span class="cr-crew-p is-alone"><span class="cr-avatar" aria-hidden="true"><i class="fas fa-user"></i></span><span class="cr-crew-t"><small>Առաքիչ՝</small><b>մենակ</b></span></span>');
        }
        if (!drivers.length && !helpers.length) return '';
        return '<div class="cr-crew" aria-label="Անձնակազմ">' + (drivers.length ? person('Վարորդ՝', drivers.join(', '), '', []) : '')
            + (drivers.length && helpers.length ? '<span class="cr-crew-sep" aria-hidden="true">·</span>' : '') + helpers.join('') + '</div>';
    }
    function carCard(c, plan, date) {
        const stat = (v, label, cls) => '<div class="cr-stat ' + (v ? cls : '') + '"><b>' + fmt(v) + '</b><span>' + esc(label) + '</span></div>';
        const done = c.full + c.partial + c.refused + c.covered;
        const rows = [...c.stops, ...c.removed];
        return '<section class="rt-card"><div class="rt-card-head"><h2 class="rt-card-title"><i class="fas fa-truck" aria-hidden="true"></i>' + esc(c.car_code) + '</h2>'
            + '<span class="rt-card-state ' + (c.total && done === c.total ? 'is-ok' : 'is-todo') + '">' + fmt(done) + ' / ' + fmt(c.total) + ' կետ</span></div>'
            + (c.error ? '<p class="rt-ferr">' + esc(c.error) + '</p>' : '')
            + crewStrip(c, plan)
            + '<div class="cr-stats">' + stat(c.full, 'Ստացված է', 'is-ok') + stat(c.partial, 'Մասնակի', 'is-warn') + stat(c.refused, 'Հրաժարում', 'is-bad')
            + stat(c.in_progress, 'Ընթացքում', 'is-warn') + stat(c.covered, 'Պատվերով արված է', 'is-ok')
            + stat(c.pending, 'Սպասում է', '') + stat(c.unreadable, 'Կոդը չի կարդացվում', 'is-warn') + stat(c.foreign, 'Այլ մեքենայի կետ', 'is-warn')
            + stat(c.flagged, 'Ուշադրություն', 'is-bad') + '</div>'
            + '<div class="cr-meta">' + (c.drivers.length ? '' : '<span>Վարորդ՝ —</span>') + '<span>Վերջին կապը՝ ' + esc(dateTime(c.last_contact)) + '</span>'
            + (c.removed.length ? '<span>Հանված կետեր՝ ' + fmt(c.removed.length) + '</span>' : '')
            + (c.gps ? '<span>GPS՝ ' + fmt(c.gps.km, 1) + ' կմ (' + fmt(c.gps.points) + ' կետ, ' + esc(timeOf(c.gps.first)) + '–' + esc(timeOf(c.gps.last)) + ')</span>' : '') + '</div>'
            + refuelsBlock(c.refuels || [])
            + (rows.length ? '<details class="rt-fold" style="margin-top:10px"><summary><span class="rt-fold-t">Կետերը</span></summary><div class="rt-fold-body"><div class="rt-table-scroll"><table class="rt-table cr-small">'
              + '<thead><tr><th scope="col">№</th><th scope="col">Հաճախորդ</th><th scope="col">Ապրանքագիր</th><th scope="col">Վճարում</th><th scope="col">Վճարելու է</th><th scope="col">Վճարված է</th><th scope="col">Վիճակ</th></tr></thead><tbody>'
              + rows.map(s => '<tr class="' + ((s.flags || []).includes('merge_conflict') ? 'cr-row-warn' : s.removed ? 'is-closed' : '') + '"><td>' + (s.removed ? '—' : fmt(s.seq)) + '</td><td>' + esc(s.customer) + '</td><td>' + docLink(date, s.stop_id, s.doc_number) + '</td><td>' + esc(COLLECT[s.collect] || '')
                + '</td><td class="cr-num-cell" title="Ապրանքագիր՝ ' + esc(money(s.amount_due)) + '">' + money(s.due) + '</td><td class="cr-num-cell">' + money(s.paid) + '</td><td>' + statusBadge(s) + tareText(s.tare)
                + ((s.flags || []).length ? ' <span class="cr-muted">' + esc(flagText(s.flags)) + '</span>' : '') + '</td></tr>').join('')
              + '</tbody></table></div></div></details>' : '')
            + '</section>';
    }

    // ---------- Маркировка ----------
    const marks = { rows: [] };
    function marksQuery() {
        const p = new URLSearchParams();
        if ($('crMarksQ').value.trim()) p.set('q', $('crMarksQ').value.trim());
        if ($('crMarksFrom').value) p.set('from', $('crMarksFrom').value);
        if ($('crMarksTo').value) p.set('to', $('crMarksTo').value);
        return p.toString();
    }
    async function loadMarks(ev) {
        if (ev) ev.preventDefault();
        $('crMarksCsv').href = '/api/courier/admin/marks.csv?' + marksQuery();
        try {
            const d = await api('/api/courier/admin/marks?' + marksQuery());
            marks.rows = d.rows;
            $('crMarksNote').textContent = d.rows.length ? 'Գտնվել է՝ ' + fmt(d.rows.length) + (d.rows.length >= d.limit ? ' (ցույց է տրված առաջին ' + fmt(d.limit) + '-ը)' : '') : 'Ոչինչ չի գտնվել';
            $('crMarksRows').innerHTML = d.rows.map(r => '<tr class="' + (r.cancelled ? 'is-closed' : '') + '"><td>' + esc(dateTime(r.at)) + '</td>'
                + '<td class="cr-mono">' + esc((r.raw || '').replace(/\u001d/g, '<GS>')) + (r.duplicate_elsewhere ? ' ' + badge('կրկնված', 'b-danger') : '') + (r.cancelled ? ' ' + badge('չեղարկված', 'b-none') : '') + '</td>'
                + '<td class="cr-mono">' + esc(r.gtin || '') + '<br>' + esc(r.serial || '') + '</td><td>' + esc(r.product_name || '') + '</td>'
                + '<td>' + esc(r.customer_name || '') + (r.tax_id ? '<br><span class="cr-muted">ՀՎՀՀ ' + esc(r.tax_id) + '</span>' : '') + '</td>'
                + '<td>' + docLink(r.known ? r.date : null, r.stop_id, r.doc_number) + (r.split ? ' ' + badge('բաժանված', 'b-warn') : '') + (r.order_number ? '<br><span class="cr-muted">պատվեր ' + esc(r.order_number) + '</span>' : '')
                    + (r.old_invoice_number ? '<br><span class="cr-muted">նախկին ապրանքագիր ' + esc(r.old_invoice_number) + '</span>' : '') + '</td><td>' + esc(r.driver_name || '') + '<br><span class="cr-muted">' + esc(r.car_code) + '</span></td>'
                + '<td>' + (r.kind === 'return' ? 'վերադարձ' : 'վաճառք') + (r.is_group ? ', տուփ (' + fmt(r.units) + ')' : '') + '</td></tr>').join('');
        } catch (e) { showError(e.message); }
    }
    function exportMarksExcel() {
        if (typeof window.XLSX === 'undefined') { showError('Excel-ի գրադարանը չբեռնվեց (cdn.jsdelivr.net-ը հասանելի չէ) — օգտագործեք CSV'); return; }
        // սյուները՝ ինչպես CSV-ում (views.MARK_COLUMNS); «Ապրանքագիր»՝ պատվերի բոլոր ապրանքագրերը
        const head = ['Կոդ', 'GTIN', 'Սերիական համար', 'Ապրանքի կոդ', 'Ապրանք', 'Հաճախորդի կոդ', 'Հաճախորդ', 'ՀՎՀՀ', 'Ապրանքագիր', 'Պատվեր', 'Նախկին ապրանքագիր', 'Բաժանված պատվեր',
            'Ամսաթիվ', 'Ժամանակ', 'Վարորդ', 'Մեքենա', 'Տեսակ', 'Խմբային', 'Հատ', 'Կրկնված այլ տեղ', 'Չեղարկված'];
        const rows = marks.rows.map(r => [(r.raw || '').replace(/\u001d/g, '<GS>'), r.gtin, r.serial, r.product_code, r.product_name, r.customer_code, r.customer_name,
            r.tax_id, r.doc_number, r.order_number, r.old_invoice_number, r.split ? 'այո' : '', r.date, r.at, r.driver_name, r.car_code, r.kind === 'return' ? 'վերադարձ' : 'վաճառք',
            r.is_group ? 'այո' : '', r.units, r.duplicate_elsewhere ? 'այո' : '', r.cancelled ? 'այո' : '']);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([head, ...rows]), 'Մակնշում');
        XLSX.writeFile(wb, 'makanshum_' + ($('crMarksFrom').value || 'all') + '_' + ($('crMarksTo').value || 'all') + '.xlsx');
        announce('Excel ֆայլը ներբեռնված է');
    }

    // ---------- Настройки ----------
    const set = { data: null, changed: new Map() };
    async function loadSettings() {
        try {
            set.data = await api('/api/courier/admin/settings');
            set.changed.clear();
            renderSettings();
        } catch (e) { showError(e.message); }
    }
    function productState(p) { return set.changed.get(p.id) || { marked: p.marked, pack_qty: p.pack_qty }; }
    function renderProducts() {
        const d = set.data;
        const q = $('crProdQ').value.trim().toLowerCase();
        const onlySold = $('crProdSold').checked, onlyMarked = $('crProdMarked').checked;
        const rows = d.products.filter(p => !p.container && (!onlySold || p.sold_90) && (!onlyMarked || productState(p).marked)
            && (!q || p.code.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)));
        $('crProdErr').textContent = d.products_erp_failed ? 'ERP-ն հասանելի չէ՝ ապրանքների ցուցակը չբեռնվեց։' : '';
        $('crProdRows').innerHTML = rows.length ? rows.map(p => {
            const s = productState(p);
            return '<tr class="' + (p.closed ? 'is-closed' : '') + '"><td class="cr-mono">' + esc(p.code) + '</td><td>' + esc(p.name)
                + (p.marked_erp ? ' ' + badge('ERP', 'b-erp') : '') + '</td><td class="cr-num-cell">' + (p.sold_90 ? fmt(p.sold_qty_90) : '—') + '</td>'
                + '<td><input type="checkbox" data-mark="' + p.id + '"' + (s.marked ? ' checked' : '') + ' aria-label="Մակնշվող՝ ' + esc(p.name) + '"></td>'
                + '<td><input class="rt-input cr-num" data-pack="' + p.id + '" inputmode="numeric" value="' + (s.pack_qty === null ? '' : esc(s.pack_qty)) + '"'
                + ' placeholder="' + (p.pack_qty_erp ? esc(p.pack_qty_erp) : '') + '" aria-label="Հատ տուփում՝ ' + esc(p.name) + '"></td></tr>';
        }).join('') : '<tr><td colspan="5" class="cr-muted">Ապրանքներ չկան</td></tr>';
        $('crProdSave').disabled = !set.changed.size;
    }
    function reasonItem(kind, r) {
        return '<li class="' + (r.active ? '' : 'is-off') + '"><span class="grow">' + esc(r.text) + '</span>'
            + '<button type="button" class="rt-linkbtn" data-reason="' + esc(kind) + '" data-rid="' + esc(r.id) + '" data-text="' + esc(r.text) + '" data-active="' + (r.active ? '0' : '1') + '">'
            + (r.active ? 'Թաքցնել' : 'Վերադարձնել') + '</button></li>';
    }
    function renderSettings() {
        const d = set.data;
        renderProducts();
        $('crTareList').innerHTML = d.tare_custom.length ? d.tare_custom.map(t => '<li class="' + (t.active ? '' : 'is-off') + '"><span class="grow">' + esc(t.name) + '</span>'
            + '<button type="button" class="rt-linkbtn" data-tare="' + t.id + '" data-name="' + esc(t.name) + '" data-active="' + (t.active ? '0' : '1') + '">'
            + (t.active ? 'Թաքցնել' : 'Վերադարձնել') + '</button></li>').join('') : '<li class="cr-muted">Լրացուցիչ տարա չկա</li>';
        $('crReasonRefuse').innerHTML = d.reasons.refuse.map(r => reasonItem('refuse', r)).join('');
        $('crReasonReturn').innerHTML = d.reasons.return.map(r => reasonItem('return', r)).join('');
        $('crApkCurrent').textContent = d.release
            ? 'Հիմա տերմինալներին տրվում է՝ ' + d.release.version_name + ' (version_code ' + d.release.version_code + ', ' + fmt(d.release.size / 1048576, 1) + ' ՄԲ, ' + dateTime(d.release.uploaded_at) + ')։ Նորը բեռնելիս version_code-ը պետք է մեծ լինի։'
            : 'APK դեռ բեռնված չէ։ Տերմինալները ստուգում են թարմացումը մուտք գործելիս։';
    }
    function onProductInput(ev) {
        const t = ev.target;
        const id = Number(t.dataset.mark || t.dataset.pack);
        if (!id) return;
        const p = set.data.products.find(x => x.id === id);
        const s = { ...productState(p) };
        if (t.dataset.mark) s.marked = t.checked;
        else {
            const raw = t.value.trim();
            s.pack_qty = raw === '' ? null : Number(raw.replace(',', '.'));
        }
        set.changed.set(id, s);
        $('crProdSave').disabled = false;
    }
    async function saveProducts() {
        const items = [...set.changed.entries()].map(([id, s]) => ({ product_id: id, marked: s.marked, pack_qty: s.pack_qty }));
        if (items.some(i => i.pack_qty !== null && (!Number.isFinite(i.pack_qty) || i.pack_qty < 1))) { $('crProdErr').textContent = 'Տուփում հատերի քանակը պետք է լինի 1 կամ ավելի'; return; }
        try {
            await api('/api/courier/admin/settings/products', { json: { items } });
            announce('Պահպանված է');
            await loadSettings();
        } catch (e) { $('crProdErr').textContent = e.message; }
    }
    async function postAndReload(url, json) {
        try { await api(url, { json }); announce('Պահպանված է'); await loadSettings(); } catch (e) { showError(e.message); }
    }
    async function uploadApk(ev) {
        ev.preventDefault();
        $('crApkErr').textContent = '';
        const f = $('crApkFile').files[0];
        if (!f) return;
        const form = new FormData();
        form.append('file', f);
        form.append('version_code', $('crApkCode').value.trim());
        form.append('version_name', $('crApkName').value.trim());
        try {
            await api('/api/courier/admin/apk', { form });
            $('crApkForm').reset();
            announce('APK-ն բեռնված է');
            await loadSettings();
        } catch (e) { $('crApkErr').textContent = e.message; }
    }

    const LOADERS = { drivers: loadDrivers, today: loadToday, marks: loadMarks, settings: loadSettings };

    // ---------- События ----------
    function init() {
        $('crTodayDate').value = today();
        $('crDriverForm').addEventListener('submit', saveDriver);
        $('crDriverNew').addEventListener('click', resetDriverForm);
        $('crTermForm').addEventListener('submit', createTerminal);
        $('crQrHide').addEventListener('click', () => { $('crQrBox').hidden = true; $('crQr').innerHTML = ''; $('crQrText').textContent = ''; $('crQrAdminPin').textContent = ''; $('crQrFor').textContent = ''; });
        $('crPane-drivers').addEventListener('click', (ev) => {
            const b = ev.target.closest('button');
            if (!b) return;
            if (b.dataset.edit) editDriver(Number(b.dataset.edit));
            if (b.dataset.revoke) revokeTerminal(Number(b.dataset.revoke));
            if (b.dataset.reissue) reissueTerminal(Number(b.dataset.reissue));
            if (b.dataset.move) toggleMove(Number(b.dataset.move), b.getAttribute('aria-expanded') !== 'true');
            if (b.dataset.moveCancel) toggleMove(Number(b.dataset.moveCancel), false);
            if (b.dataset.attach) attachTerminal(b.dataset.attach);
        });
        $('crPane-drivers').addEventListener('submit', moveTerminal);
        $('crTodayRefresh').addEventListener('click', loadToday);
        $('crTodayDate').addEventListener('change', loadToday);
        $('crMarksForm').addEventListener('submit', loadMarks);
        $('crMarksXlsx').addEventListener('click', exportMarksExcel);
        ['crProdQ', 'crProdSold', 'crProdMarked'].forEach(id => $(id).addEventListener('input', renderProducts));
        $('crProdRows').addEventListener('change', onProductInput);
        $('crProdSave').addEventListener('click', saveProducts);
        $('crTareForm').addEventListener('submit', (ev) => { ev.preventDefault(); postAndReload('/api/courier/admin/settings/tare', { name: $('crTareName').value, active: true }); });
        $('crTareList').addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-tare]');
            if (b) postAndReload('/api/courier/admin/settings/tare', { id: Number(b.dataset.tare), name: b.dataset.name, active: b.dataset.active === '1' });
        });
        document.querySelectorAll('form.cr-inline[data-kind]').forEach(f => f.addEventListener('submit', (ev) => {
            ev.preventDefault();
            postAndReload('/api/courier/admin/settings/reasons', { kind: f.dataset.kind, text: f.querySelector('input').value, active: true });
        }));
        ['crReasonRefuse', 'crReasonReturn'].forEach(id => $(id).addEventListener('click', (ev) => {
            const b = ev.target.closest('[data-reason]');
            if (b) postAndReload('/api/courier/admin/settings/reasons', { kind: b.dataset.reason, id: b.dataset.rid, text: b.dataset.text, active: b.dataset.active === '1' });
        }));
        $('crApkForm').addEventListener('submit', uploadApk);
        window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
        showTab(location.hash.slice(1));
    }
    document.addEventListener('DOMContentLoaded', init);
})();

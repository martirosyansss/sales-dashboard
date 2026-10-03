/* «Ավտոտնակ» /routes/garage — журнал ремонтов и пробега машин (ответ владельца №53, docs/plans/garage-journal-plan.md).
   API: GET /api/routes/garage (машины с последним показанием, итоги за 12 месяцев с ремонтом ֏/км, расходы по месяцам),
   GET /api/routes/garage/entries?car=&month=&deleted= (записи), POST /api/routes/garage/entries (новая или правка, "id"),
   POST /api/routes/garage/entries/delete {id}, POST /api/routes/garage/odometers {items: [{car_code, day, odometer_km}]}.
   Ошибки сервера — по-армянски, по полям. Всё, что пришло с сервера, выводится только через textContent.
   CSRF-заголовок к fetch добавляет base_v2.html. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v))) ? null : Number(v);
    const fmt = (v, d = 0) => { const n = num(v); return n === null ? '—' : n.toLocaleString('ru-RU', { maximumFractionDigits: d }); };
    const dayHy = (iso) => (typeof iso === 'string' && iso.length >= 10 ? iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4) : '—');
    const MONTHS = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    const monthHy = (key) => { const m = /^(\d{4})-(\d{2})$/.exec(key || ''); return m ? MONTHS[+m[2] - 1] + ' ' + m[1] : key; };
    const KIND = { repair: 'Վերանորոգում', accident: 'Վթար', fixed: 'Ապահովագրություն / հարկ', odometer: 'Վազք' };
    const KIND_CLASS = { repair: 'b-ok', accident: 'b-danger', fixed: 'b-erp', odometer: 'b-none' };

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else if (k === 'dataset') Object.assign(el.dataset, v);
            else if (typeof v === 'boolean') el[k] = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => {
            if (c === null || c === undefined || c === false) return;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        });
        return el;
    }
    const icon = (cls) => h('i', { class: 'fas ' + cls, 'aria-hidden': 'true' });

    const state = { data: null, entries: [], editing: null, busy: false };

    function announce(text) { $('gjStatus').textContent = ''; setTimeout(() => { $('gjStatus').textContent = text; }, 30); }
    function showError(text) {
        $('gjAlert').hidden = !text;
        $('gjAlertText').textContent = text || '';
    }

    class ApiError extends Error {
        constructor(message, errors) { super(message); this.errors = errors || {}; }
    }
    // Ответы сервера — по-армянски: проверки журнала уже армянские (их и показываем, по полям); прочее (вход, доступ,
    // CSRF, не JSON, сбой сервера — тексты дашборда по-русски) — своим армянским текстом по коду ответа.
    const HY = /[\u0531-\u058F]/;
    function httpError(status, body) {
        const text = body && typeof body.error === 'string' ? body.error : '';
        if (status === 403) return text === 'csrf' ? 'Էջը հնացել է՝ թարմացրեք այն և կրկնեք։' : 'Այս գործողությունը ձեզ թույլատրված չէ։';
        if ((status === 400 || status === 404) && HY.test(text)) return text;
        if (status === 400 || status === 415) return 'Հարցումը չընդունվեց։ Թարմացրեք էջը և կրկնեք։';
        if (status === 404) return 'Չի գտնվել։ Թարմացրեք էջը։';
        if (status === 503) return 'Տվյալների բազան ժամանակավորապես հասանելի չէ։ Կրկնեք մի փոքր ուշ։';
        return 'Սերվերի սխալ (' + status + ')։ Կրկնեք մի փոքր ուշ։';
    }
    async function api(url, json) {
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (json !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new ApiError('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
        if (resp.status === 401) {   // сессия кончилась — на вход, потом обратно сюда
            window.location.assign('/login?next=' + encodeURIComponent('/routes/garage'));
            throw new ApiError('Մուտք գործեք նորից։');
        }
        try { body = await resp.json(); } catch (e) { /* не JSON */ }
        if (!resp.ok || !body || body.success === false) {
            const errors = Object.fromEntries(Object.entries((body && body.errors) || {})
                .filter(([, v]) => typeof v === 'string' && HY.test(v)));
            throw new ApiError(httpError(resp.status, body), errors);
        }
        return body;
    }

    const truckName = (code) => {
        const t = state.data && state.data.trucks.find(x => x.car_code === code);
        return t && t.name ? code + ' · ' + t.name : code;
    };

    // ---------- вкладки ----------
    const TABS = [['gjTabCosts', 'gjCosts'], ['gjTabOdo', 'gjOdo'], ['gjTabSum', 'gjSum']];
    function selectTab(tabId, focus) {
        TABS.forEach(([t, p]) => {
            const on = t === tabId;
            $(t).setAttribute('aria-selected', on ? 'true' : 'false');
            $(t).tabIndex = on ? 0 : -1;
            $(p).hidden = !on;
        });
        if (focus) $(tabId).focus();
        try { localStorage.setItem('gjTab', tabId); } catch (e) { /* без памяти вкладки */ }
    }
    TABS.forEach(([t], i) => {
        $(t).addEventListener('click', () => selectTab(t));
        $(t).addEventListener('keydown', (ev) => {
            const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
            if (!step) return;
            ev.preventDefault();
            selectTab(TABS[(i + step + TABS.length) % TABS.length][0], true);
        });
    });

    // ---------- форма записи ----------
    function fieldErrors(errors) {
        document.querySelectorAll('#gjForm [data-for]').forEach(el => { el.textContent = (errors || {})[el.dataset.for] || ''; });
        const map = { car_code: 'gjCar', day: 'gjDay', kind: 'gjKind', what: 'gjWhat', amount_amd: 'gjAmount', odometer_km: 'gjOdoKm', note: 'gjNote' };
        Object.entries(map).forEach(([k, id]) => {
            const bad = !!(errors && errors[k]);
            $(id).classList.toggle('is-invalid', bad);
            if (bad) $(id).setAttribute('aria-invalid', 'true'); else $(id).removeAttribute('aria-invalid');
        });
        const first = errors && Object.keys(map).find(k => errors[k]);
        if (first) $(map[first]).focus();
    }

    function syncKind() {
        const odo = $('gjKind').value === 'odometer';
        $('gjAmountBox').hidden = odo;
        $('gjWhat').placeholder = odo ? 'Ըստ ցանկության' : 'Օրինակ՝ արգելակի կոճղակներ, յուղի փոխում';
        $('gjWhatBox').querySelector('label').textContent = odo ? 'Ինչ է արվել (ըստ ցանկության)' : 'Ինչ է արվել';
    }

    function syncOdoHint() {
        const t = state.data && state.data.trucks.find(x => x.car_code === $('gjCar').value);
        $('gjOdoHint').textContent = t && t.last_km !== null
            ? 'Վերջին հայտնի ցուցմունքը՝ ' + fmt(t.last_km) + ' կմ (' + dayHy(t.last_day) + ')'
            : 'Այս մեքենայի վազքը դեռ գրանցված չէ';
    }

    function fillCars() {
        const trucks = state.data.trucks;
        const cur = $('gjCar').value, curFilter = $('gjFilterCar').value;
        const active = trucks.filter(t => t.active);
        $('gjCar').replaceChildren(h('option', { value: '', text: 'Ընտրեք մեքենան' }),
            ...active.map(t => h('option', { value: t.car_code, text: t.car_code + (t.name ? ' · ' + t.name : '') })));
        // правка записи машины, которая уже не в работе, — её номер всё равно в списке
        if (cur && !active.some(t => t.car_code === cur)) $('gjCar').append(h('option', { value: cur, text: truckName(cur) }));
        $('gjCar').value = cur;
        $('gjFilterCar').replaceChildren(h('option', { value: '', text: 'Բոլոր մեքենաները' }),
            ...trucks.map(t => h('option', { value: t.car_code, text: t.car_code + (t.name ? ' · ' + t.name : '') })));
        $('gjFilterCar').value = curFilter;
        syncOdoHint();
    }

    function resetForm() {
        state.editing = null;
        $('gjForm').reset();
        $('gjDay').value = state.data ? state.data.today : '';
        $('gjDay').max = state.data ? state.data.today : '';
        $('gjFormTitle').replaceChildren(icon('fa-plus'), 'Նոր գրառում');
        $('gjCancel').hidden = true;
        fieldErrors(null);
        syncKind();
        syncOdoHint();
    }

    function editEntry(e) {
        state.editing = e.id;
        selectTab('gjTabCosts');
        if (![...$('gjCar').options].some(o => o.value === e.car_code)) $('gjCar').append(h('option', { value: e.car_code, text: truckName(e.car_code) }));
        $('gjCar').value = e.car_code;
        $('gjDay').value = e.day;
        $('gjKind').value = e.kind;
        $('gjWhat').value = e.what || '';
        $('gjAmount').value = e.kind === 'odometer' ? '' : String(e.amount_amd);
        $('gjOdoKm').value = String(e.odometer_km);
        $('gjNote').value = e.note || '';
        $('gjFormTitle').replaceChildren(icon('fa-pen'), 'Գրառման փոփոխում · ' + dayHy(e.day));
        $('gjCancel').hidden = false;
        fieldErrors(null);
        syncKind();
        syncOdoHint();
        $('gjForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
        $('gjCar').focus({ preventScroll: true });
    }

    function intOrRaw(v) {
        const s = String(v).trim().replace(/\s+/g, '');
        if (s === '') return null;
        return /^-?\d+$/.test(s) ? Number(s) : s;   // нечисло уходит как есть — сервер скажет, что не так
    }

    async function saveEntry(ev) {
        ev.preventDefault();
        if (state.busy) return;
        const kind = $('gjKind').value;
        const body = {
            car_code: $('gjCar').value, day: $('gjDay').value, kind,
            what: $('gjWhat').value.trim() || null, odometer_km: intOrRaw($('gjOdoKm').value), note: $('gjNote').value.trim() || null,
        };
        if (kind !== 'odometer') body.amount_amd = intOrRaw($('gjAmount').value);
        if (state.editing !== null) body.id = state.editing;
        const badNum = {};   // нечисло в числовом поле — своя ошибка, а не «пусто»
        if ($('gjOdoKm').validity.badInput) badNum.odometer_km = 'Գրեք ամբողջ թիվ՝ կիլոմետր';
        if (kind !== 'odometer' && $('gjAmount').validity.badInput) badNum.amount_amd = 'Գրեք ամբողջ թիվ՝ դրամ';
        if (Object.keys(badNum).length) { fieldErrors(badNum); return; }
        state.busy = true;
        $('gjSave').disabled = true;
        fieldErrors(null);
        try {
            await api('/api/routes/garage/entries', body);
            const wasEdit = state.editing !== null;
            const car = body.car_code;
            await reload();
            resetForm();
            $('gjCar').value = car;   // следующая запись — обычно та же машина
            syncOdoHint();
            announce(wasEdit ? 'Գրառումը փոխվեց' : 'Գրառումը պահպանվեց');
            showError('');
        } catch (e) {
            fieldErrors(e.errors);
            if (!e.errors || !Object.keys(e.errors).some(k => k !== '_')) $('gjFormErr').textContent = e.message;
        } finally {
            state.busy = false;
            $('gjSave').disabled = false;
        }
    }

    async function deleteEntry(e) {
        const what = (KIND[e.kind] || e.kind) + ', ' + dayHy(e.day) + ', ' + truckName(e.car_code)
            + (e.kind === 'odometer' ? '' : ', ' + fmt(e.amount_amd) + ' ֏');
        if (!window.confirm('Ջնջե՞լ գրառումը՝ ' + what + '։')) return;
        try {
            await api('/api/routes/garage/entries/delete', { id: e.id });
            if (state.editing === e.id) resetForm();
            await reload();
            announce('Գրառումը ջնջվեց');
        } catch (err) {
            showError(err.message);
        }
    }

    // ---------- список записей ----------
    async function loadEntries() {
        const q = new URLSearchParams();
        if ($('gjFilterCar').value) q.set('car', $('gjFilterCar').value);
        if ($('gjFilterMonth').value) q.set('month', $('gjFilterMonth').value);
        if ($('gjShowDeleted') && $('gjShowDeleted').checked) q.set('deleted', '1');
        const body = await api('/api/routes/garage/entries' + (q.toString() ? '?' + q : ''));
        state.entries = body.entries;
        renderEntries(body.total_amd);
    }

    function renderEntries(total) {
        const rows = state.entries;
        const live = rows.filter(e => !e.deleted_at);
        $('gjTotal').textContent = rows.length
            ? 'Ընդամենը՝ ' + fmt(total) + ' ֏ · ' + fmt(live.length) + ' գրառում' + (rows.length > live.length ? ' (ջնջված՝ ' + fmt(rows.length - live.length) + ')' : '')
            : '';
        if (!rows.length) {
            $('gjRows').replaceChildren(h('tr', {}, h('td', { colspan: 7, class: 'rt-empty', text: 'Այս ընտրությամբ գրառումներ չկան' })));
            return;
        }
        $('gjRows').replaceChildren(...rows.map(e => {
            const gone = !!e.deleted_at;
            // удалённая — «Ջնջված»; прежняя версия изменённой записи (replaced_by) — «Փոփոխված»: новые значения — в живой строке
            const acts = gone
                ? h('span', { class: 'gj-gone' + (e.replaced_by ? ' is-changed' : ''), text: (e.replaced_by ? 'Փոփոխված ' : 'Ջնջված ')
                    + dayHy(e.deleted_at) + (e.deleted_by ? ' · ' + e.deleted_by : '') })
                : h('span', { class: 'gj-row-acts' },
                    h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Փոխել գրառումը ' + dayHy(e.day), dataset: { act: 'edit', id: e.id } }, icon('fa-pen'), 'Փոխել'),
                    h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Ջնջել գրառումը ' + dayHy(e.day), dataset: { act: 'del', id: e.id } }, icon('fa-trash-can'), 'Ջնջել'));
            return h('tr', { class: gone ? 'is-closed' : null },
                h('td', { class: 'gj-day w-half', 'data-label': 'Ամսաթիվ', text: dayHy(e.day) }),
                h('td', { class: 'gj-car', 'data-label': 'Մեքենա', text: truckName(e.car_code) }),
                h('td', { class: 'w-half', 'data-label': 'Տեսակ' }, h('span', { class: 'rt-badge ' + (KIND_CLASS[e.kind] || ''), text: KIND[e.kind] || e.kind })),
                h('td', { class: 'gj-what', 'data-label': 'Ինչ է արվել' }, e.what || '—', e.note ? h('small', { text: e.note }) : null),
                h('td', { class: 'gj-num w-half', 'data-label': 'Գումար, ֏', text: e.kind === 'odometer' ? '—' : fmt(e.amount_amd) }),
                h('td', { class: 'gj-num w-half', 'data-label': 'Սպիդոմետր, կմ', text: fmt(e.odometer_km) }),
                h('td', { class: 'gj-acts' }, acts));
        }));
    }
    $('gjRows').addEventListener('click', (ev) => {
        const btn = ev.target.closest('button[data-act]');
        if (!btn) return;
        const e = state.entries.find(x => x.id === Number(btn.dataset.id));
        if (!e) return;
        if (btn.dataset.act === 'edit') editEntry(e); else deleteEntry(e);
    });

    // ---------- пробег всех машин ----------
    function renderOdo() {
        const trucks = state.data.trucks.filter(t => t.active);
        $('gjOdoDay').value = $('gjOdoDay').value || state.data.today;
        $('gjOdoDay').max = state.data.today;
        if (!trucks.length) {
            $('gjOdoRows').replaceChildren(h('tr', {}, h('td', { colspan: 3, class: 'rt-empty', text: 'Աշխատող մեքենաներ չկան' })));
            return;
        }
        $('gjOdoRows').replaceChildren(...trucks.map(t => {
            const id = 'gjOdo-' + t.car_code.replace(/[^\w-]/g, '_');
            const input = h('input', { id, class: 'rt-input', type: 'number', inputmode: 'numeric', min: 0, max: 2000000, step: 1,
                placeholder: t.last_km !== null ? fmt(t.last_km) : 'կմ', 'aria-describedby': id + '-err', dataset: { car: t.car_code } });
            return h('tr', {},
                h('td', { class: 'rt-cell-name' }, h('label', { for: id, class: 'n', text: t.car_code }), h('span', { class: 'c', text: t.name || '' })),
                h('td', { class: 'w-half', 'data-label': 'Վերջին ցուցմունքը' }, t.last_km !== null
                    ? [h('b', { class: 'gj-km', text: fmt(t.last_km) + ' կմ' }), h('small', { class: 'gj-sub', text: dayHy(t.last_day) })]
                    : h('span', { class: 'gj-sub', text: 'դեռ չկա' })),
                h('td', { class: 'w-half', 'data-label': 'Նոր ցուցմունք, կմ' }, input, h('span', { id: id + '-err', class: 'rt-ferr' })));
        }));
    }

    async function saveOdo(ev) {
        ev.preventDefault();
        if (state.busy) return;
        const day = $('gjOdoDay').value;
        const inputs = [...$('gjOdoRows').querySelectorAll('input[data-car]')];
        inputs.forEach(inp => { inp.classList.remove('is-invalid'); inp.removeAttribute('aria-invalid'); $(inp.id + '-err').textContent = ''; });
        $('gjOdoErr').textContent = '';
        // нечисло в поле (badInput: value пустое) — ошибка строки, а не молчаливый пропуск
        const bad = inputs.filter(inp => inp.validity && inp.validity.badInput);
        if (bad.length) {
            bad.forEach(inp => {
                inp.classList.add('is-invalid');
                inp.setAttribute('aria-invalid', 'true');
                $(inp.id + '-err').textContent = 'Գրեք ամբողջ թիվ՝ կիլոմետր';
            });
            $('gjOdoErr').textContent = 'Ուղղեք նշված տողերը՝ ոչինչ չի պահպանվել։';
            bad[0].focus();
            return;
        }
        const filled = inputs.filter(inp => inp.value.trim() !== '');
        if (!filled.length) { $('gjOdoErr').textContent = 'Լրացրեք գոնե մեկ մեքենայի վազքը'; return; }
        state.busy = true;
        $('gjOdoSave').disabled = true;
        try {
            const body = await api('/api/routes/garage/odometers',
                { items: filled.map(inp => ({ car_code: inp.dataset.car, day, odometer_km: intOrRaw(inp.value) })) });
            const errors = body.errors || {};
            // строки с ошибкой остаются заполненными и с причиной; записанные — очищаются (таблица перерисовывается)
            const failed = filled.map((inp, i) => [inp.dataset.car, inp.value, errors[String(i)]]).filter(x => x[2]);
            const saved = (body.saved || []).length;
            await reload();
            failed.forEach(([car, value, text]) => {
                const inp = $('gjOdoRows').querySelector('input[data-car="' + CSS.escape(car) + '"]');
                if (!inp) return;
                inp.value = value;
                inp.classList.add('is-invalid');
                inp.setAttribute('aria-invalid', 'true');
                $(inp.id + '-err').textContent = text;
            });
            $('gjOdoErr').textContent = failed.length ? 'Պահպանվեց՝ ' + saved + ', սխալ՝ ' + failed.length + '։ Ուղղեք նշված տողերը։' : '';
            announce('Վազքը պահպանվեց՝ ' + saved + ' մեքենա');
        } catch (e) {
            $('gjOdoErr').textContent = e.message;
        } finally {
            state.busy = false;
            $('gjOdoSave').disabled = false;
        }
    }

    // ---------- итоги ----------
    function statusBadge(r) {
        const g = r.garage;
        if (!g || g.status === 'none') return h('span', { class: 'rt-badge b-none', text: 'Վազք չկա' });
        if (g.status === 'ready') return h('span', { class: 'rt-badge b-ok', text: 'Հաշվարկում է' });
        if (g.status === 'low_km') return h('span', { class: 'rt-badge b-warn', text: 'Քիչ կմ' });
        if (g.status === 'no_repairs') return h('span', { class: 'rt-badge b-warn', text: 'Վերանորոգում չի գրանցված',
            title: 'Վերանորոգում չկա՝ հաշվարկում է կարգավորումների «մաշվածությունը»' });
        return h('span', { class: 'rt-badge b-warn', text: 'Կուտակվում է՝ ' + g.months + ' / ' + g.ready_months + ' ամիս' });
    }

    function renderSummary() {
        const d = state.data, rules = d.rules;
        $('gjSumLead').textContent = 'Վերանորոգում ֏/կմ = վերանորոգման ծախսը ÷ վազքը նույն ժամանակահատվածում։ Առաքման հաշվարկում է, երբ վազքը ծածկում է '
            + rules.ready_months + ' ամիս և առնվազն ' + fmt(rules.ready_km) + ' կմ, և այդ ընթացքում գրանցված է գոնե մեկ վերանորոգում։ '
            + 'Մինչ այդ հաշվարկում է կարգավորումների «մաշվածությունը»։';
        if (!d.summary.length) {
            $('gjSumRows').replaceChildren(h('tr', {}, h('td', { colspan: 8, class: 'rt-empty', text: 'Մեքենաներ չկան' })));
        } else {
            $('gjSumRows').replaceChildren(...d.summary.map(r => {
                const g = r.garage;
                const last = r.last_day
                    ? [h('span', { text: dayHy(r.last_day) }), h('small', { class: 'gj-sub', text: fmt(r.days_since_last) + ' օր առաջ' })]
                    : [];
                if (r.stale) last.push(h('span', { class: 'rt-badge b-warn gj-stale', text: 'Լրացրեք վազքը' }));
                const price = g && g.price !== null
                    ? [h('b', { class: 'gj-price', text: fmt(g.price, 1) }), h('small', { class: 'gj-sub', text: fmt(g.cost_amd) + ' ֏ ÷ ' + fmt(g.km) + ' կմ' })]
                    : [h('span', { text: '—' })];
                return h('tr', { class: r.active ? null : 'is-closed' },
                    h('td', { class: 'rt-cell-name' }, h('span', { class: 'n', text: r.car_code }), h('span', { class: 'c', text: r.name || (r.active ? '' : 'չի աշխատում') })),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Վերանորոգում, ֏', text: g ? fmt(g.repair_amd) : '—' }),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Վթար, ֏', text: g ? fmt(g.accident_amd) : '—' }),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Ապահովագրություն, հարկ, ֏', text: g ? fmt(g.fixed_amd) : '—' }),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Կմ', text: g && g.status !== 'none' ? fmt(g.km) : '—' }),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Վերանորոգում, ֏/կմ' }, ...price),
                    h('td', { class: 'w-half', 'data-label': 'Կարգավիճակ' }, statusBadge(r)),
                    h('td', { class: 'w-half', 'data-label': 'Վերջին վազքը' }, ...last));
            }));
        }
        $('gjMonthRows').replaceChildren(...d.months.slice().reverse().map(m => h('tr', {},
            h('td', { class: 'rt-cell-name' }, h('span', { class: 'n', text: monthHy(m.month) })),
            h('td', { class: 'gj-num w-half', 'data-label': 'Վերանորոգում, ֏', text: fmt(m.repair) }),
            h('td', { class: 'gj-num w-half', 'data-label': 'Վթար, ֏', text: fmt(m.accident) }),
            h('td', { class: 'gj-num w-half', 'data-label': 'Ապահովագրություն, հարկ, ֏', text: fmt(m.fixed) }),
            h('td', { class: 'gj-num w-half', 'data-label': 'Ընդամենը, ֏' }, h('b', { text: fmt(m.total) })))));
    }

    // ---------- загрузка ----------
    async function reload() {
        state.data = await api('/api/routes/garage');
        fillCars();
        renderOdo();
        renderSummary();
        await loadEntries();
    }

    async function init() {
        let tab = 'gjTabCosts';
        try { tab = localStorage.getItem('gjTab') || tab; } catch (e) { /* без памяти вкладки */ }
        selectTab(TABS.some(([t]) => t === tab) ? tab : 'gjTabCosts');
        $('gjForm').addEventListener('submit', saveEntry);
        $('gjCancel').addEventListener('click', resetForm);
        $('gjKind').addEventListener('change', syncKind);
        $('gjCar').addEventListener('change', syncOdoHint);
        $('gjOdoForm').addEventListener('submit', saveOdo);
        const refilter = () => loadEntries().catch(e => showError(e.message));
        $('gjFilterCar').addEventListener('change', refilter);
        $('gjFilterMonth').addEventListener('change', refilter);
        if ($('gjShowDeleted')) $('gjShowDeleted').addEventListener('change', refilter);
        try {
            await reload();
            resetForm();
        } catch (e) {
            showError(e.message);
            $('gjRows').replaceChildren(h('tr', {}, h('td', { colspan: 7, class: 'rt-empty', text: 'Չհաջողվեց բեռնել' })));
        }
    }
    init();
})();

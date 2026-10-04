/* «Ավտոտնակ» /routes/garage — журнал ремонтов и пробега машин (ответ владельца №53, docs/plans/garage-journal-plan.md).
   API: GET /api/routes/garage (машины с последним показанием, итоги за 12 месяцев с ремонтом ֏/км, расходы по месяцам),
   GET /api/routes/garage/entries?car=&month=&deleted= (записи), POST /api/routes/garage/entries (новая или правка, "id"),
   POST /api/routes/garage/entries/delete {id}, POST /api/routes/garage/odometers {items: [{car_code, day, odometer_km}]},
   GET /api/routes/garage/norm?month= и /api/routes/garage/day?date=&car= (вкладка «Նորմ և փաստ»).
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

    // whatList — список «Ինչ է արվել» в форме; spreadTouched — «Բաշխել» меняли руками в этой форме, spreadAuto — срок
    // поставлен выбором двигателя / КПП; odoWhat — текст правимой записи «только пробег» (поля у неё нет)
    const state = { data: null, entries: [], editing: null, busy: false, whatList: null, spreadTouched: false,
        spreadAuto: false, odoWhat: null };

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
    const TABS = [['gjTabCosts', 'gjCosts'], ['gjTabOdo', 'gjOdo'], ['gjTabSum', 'gjSum'], ['gjTabNorm', 'gjNorm']];
    function selectTab(tabId, focus) {
        TABS.forEach(([t, p]) => {
            const on = t === tabId;
            $(t).setAttribute('aria-selected', on ? 'true' : 'false');
            $(t).tabIndex = on ? 0 : -1;
            $(p).hidden = !on;
        });
        if (focus) $(tabId).focus();
        if (tabId === 'gjTabNorm') resumeNorm();   // считается только при открытии вкладки
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
    // «Ինչ է արվել» — выбор из списка вида записи (решение владельца №63); «Այլ…» — короткий текст своими словами. В базу
    // идёт ровно текст пункта (или введённый текст): схема и проверка сервера прежние. У «только пробега» поля нет.
    const OTHER = '__other';
    const REPAIR_WHAT = ['Յուղ և ֆիլտրեր (ՏՍ)', 'Արգելակներ', 'Անվադողեր', 'Մարտկոց', 'Կախոց / ղեկ', 'Էլեկտրիկա',
        'Կցորդիչ (սցեպլենիե)', 'Շարժիչ', 'Փոխանցման տուփ', 'Թափք / ապակի'];
    const WHAT_LISTS = { repair: REPAIR_WHAT, accident: REPAIR_WHAT, fixed: ['Ապահովագրություն', 'Տեխզննում', 'Հարկ'] };
    const SPREAD_WHAT = ['Շարժիչ', 'Փոխանցման տուփ'];   // крупный узел — сразу «Բաշխել» 24 месяца
    const TIRES_WHAT = 'Անվադողեր';                      // комплект — тоже, но решает механик: только подсказка

    function fillWhat(kind) {
        const list = WHAT_LISTS[kind] || [];
        $('gjWhat').replaceChildren(h('option', { value: '', text: 'Ընտրեք…' }),
            ...list.map(text => h('option', { value: text, text })), h('option', { value: OTHER, text: 'Այլ…' }));
        state.whatList = WHAT_LISTS[kind] || null;
    }

    // запись → поле: пункт списка своего вида — он и выбран; иное — «Այլ…» с этим текстом (старый текст длиннее 120
    // знаков — поле не короче него, чтобы его можно было править)
    function setWhat(kind, what) {
        fillWhat(kind);
        const text = what || '';
        const inList = (WHAT_LISTS[kind] || []).includes(text);
        $('gjWhat').value = inList ? text : text ? OTHER : '';
        $('gjWhatOther').value = inList ? '' : text;
        $('gjWhatOther').maxLength = Math.max(120, inList ? 0 : text.length);
    }

    function whatValue() {
        return $('gjWhat').value === OTHER ? $('gjWhatOther').value.trim() : $('gjWhat').value;
    }

    // выбран пункт: «Այլ…» — поле текста (обязательное, пока видно); двигатель и КПП — «Բաշխել» 24 месяца, если срок не
    // трогали руками в этой форме (и не задан); ушли с них — авто-срок снимается. auto=false — заполнение формы (правка).
    function syncWhat(auto) {
        const repair = $('gjKind').value === 'repair', what = $('gjWhat').value;
        $('gjWhatOtherBox').hidden = what !== OTHER;
        $('gjWhatOther').required = what === OTHER;
        $('gjWhatOther').setAttribute('aria-required', String(what === OTHER));
        const big = repair && SPREAD_WHAT.includes(what);
        if (auto && !state.spreadTouched) {
            if (big && !$('gjSpread').value) { $('gjSpread').value = '24'; state.spreadAuto = true; }
            else if (!big && state.spreadAuto) { $('gjSpread').value = ''; state.spreadAuto = false; }
        }
        syncSpread();
    }

    function fieldErrors(errors) {
        document.querySelectorAll('#gjForm [data-for]').forEach(el => { el.textContent = (errors || {})[el.dataset.for] || ''; });
        const whatId = $('gjWhat').value === OTHER ? 'gjWhatOther' : 'gjWhat';
        const map = { car_code: 'gjCar', day: 'gjDay', kind: 'gjKind', what: whatId, amount_amd: 'gjAmount', spread_months: 'gjSpread', odometer_km: 'gjOdoKm', note: 'gjNote' };
        ['gjWhat', 'gjWhatOther'].filter(id => id !== whatId).forEach(id => { $(id).classList.remove('is-invalid'); $(id).removeAttribute('aria-invalid'); });
        Object.entries(map).forEach(([k, id]) => {
            const bad = !!(errors && errors[k]);
            $(id).classList.toggle('is-invalid', bad);
            if (bad) $(id).setAttribute('aria-invalid', 'true'); else $(id).removeAttribute('aria-invalid');
        });
        const first = errors && Object.keys(map).find(k => errors[k]);
        if (first) $(map[first]).focus();
    }

    // «Բաշխել» — только у ремонта; крупная сумма (от rules.spread_suggest_amd) — подсказка растянуть, но не обязаловка;
    // двигатель и КПП — пояснение по ТЕКУЩЕМУ сроку (задан — сколько месяцев, «Ոչ» — совет 24), шины — подсказка
    function syncSpread() {
        const repair = $('gjKind').value === 'repair', what = $('gjWhat').value, months = $('gjSpread').value;
        $('gjSpreadBox').hidden = !repair;
        const big = num($('gjAmount').value) !== null && num($('gjAmount').value) >= ((state.data && state.data.rules.spread_suggest_amd) || 300000);
        $('gjSpreadSuggest').hidden = !(repair && big && !$('gjSpread').value);
        const unit = repair && SPREAD_WHAT.includes(what);
        const why = unit && months ? 'Շարժիչը և փոխանցման տուփը ծառայում են տարիներ՝ ծախսը բաշխվում է ' + months + ' ամսվա վրա։'
            : unit ? 'Շարժիչը և փոխանցման տուփը ծառայում են տարիներ — խորհուրդ է տրվում բաշխել 24 ամսվա վրա։'
            : repair && what === TIRES_WHAT ? 'Եթե ամբողջ հավաքածու է — ընտրեք 24 ամիս։' : '';
        $('gjSpreadWhy').textContent = why;
        $('gjSpreadWhy').hidden = !why;
    }

    // вид записи сменился: другой список (ремонт и ДТП — общий) — пункт списка сброшен, а «Այլ…» с введённым текстом
    // остаётся; у «только пробега» поля нет
    function syncKind(auto) {
        const kind = $('gjKind').value;
        $('gjAmountBox').hidden = kind === 'odometer';
        $('gjWhatBox').hidden = kind === 'odometer';
        if (state.whatList !== (WHAT_LISTS[kind] || null)) {
            const other = $('gjWhat').value === OTHER;
            fillWhat(kind);
            $('gjWhat').value = other ? OTHER : '';
        }
        syncWhat(auto);
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
        state.spreadTouched = state.spreadAuto = false;
        state.odoWhat = null;
        setWhat($('gjKind').value, '');
        fieldErrors(null);
        syncKind(false);
        syncOdoHint();
    }

    function editEntry(e) {
        state.editing = e.id;
        selectTab('gjTabCosts');
        if (![...$('gjCar').options].some(o => o.value === e.car_code)) $('gjCar').append(h('option', { value: e.car_code, text: truckName(e.car_code) }));
        $('gjCar').value = e.car_code;
        $('gjDay').value = e.day;
        $('gjKind').value = e.kind;
        setWhat(e.kind, e.kind === 'odometer' ? '' : e.what);
        state.odoWhat = e.kind === 'odometer' ? e.what || null : null;   // у «только пробега» поля нет — текст не теряем
        state.spreadTouched = state.spreadAuto = false;
        $('gjAmount').value = e.kind === 'odometer' ? '' : String(e.amount_amd);
        $('gjSpread').value = e.spread_months ? String(e.spread_months) : '';
        $('gjOdoKm').value = String(e.odometer_km);
        $('gjNote').value = e.note || '';
        $('gjFormTitle').replaceChildren(icon('fa-pen'), 'Գրառման փոփոխում · ' + dayHy(e.day));
        $('gjCancel').hidden = false;
        fieldErrors(null);
        syncKind(false);
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
            what: kind === 'odometer' ? state.odoWhat : whatValue() || null,
            odometer_km: intOrRaw($('gjOdoKm').value), note: $('gjNote').value.trim() || null,
        };
        if (kind !== 'odometer') body.amount_amd = intOrRaw($('gjAmount').value);
        body.spread_months = kind === 'repair' && $('gjSpread').value ? Number($('gjSpread').value) : null;
        if (state.editing !== null) body.id = state.editing;
        const badNum = {};   // нечисло в числовом поле — своя ошибка, а не «пусто»; что сделано — выбрано из списка
        if (kind !== 'odometer' && !$('gjWhat').value) badNum.what = 'Ընտրեք, թե ինչ է արվել';
        else if (kind !== 'odometer' && !body.what) badNum.what = 'Գրեք կարճ, թե ինչ է արվել';
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
                h('td', { class: 'w-half', 'data-label': 'Տեսակ' }, h('span', { class: 'rt-badge ' + (KIND_CLASS[e.kind] || ''), text: KIND[e.kind] || e.kind }),
                    e.spread_months ? h('small', { class: 'gj-sub', text: 'բաշխված՝ ' + e.spread_months + ' ամիս' }) : null),
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
    // Работающие машины без показания пробега дольше rules.stale_days (или вовсе без него) — плашка и подсветка в «Վազք»;
    // «давно» решает сервер (stale по его «сегодня»), не часы браузера
    function staleTrucks() {
        return state.data.summary.filter(r => r.active && r.stale);
    }

    function renderBanner() {
        const d = state.data, stale = staleTrucks();
        const text = d.journal_empty
            ? 'Սկսեք՝ գրանցելով բոլոր մեքենաների ընթացիկ վազքը և անցած տարվա վերանորոգումները։'
            : stale.length ? stale.length + ' մեքենայի վազքը ' + d.rules.stale_days + ' օրից ավելի չի գրանցվել՝ ' + stale.map(t => t.car_code).join(', ') + '։' : '';
        $('gjBanner').hidden = !text;
        $('gjBannerText').textContent = text;
    }

    function goStale() {
        selectTab('gjTabOdo');
        const first = $('gjOdoRows').querySelector('tr.is-stale input');
        if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); first.focus({ preventScroll: true }); }
    }

    function renderOdo() {
        const trucks = state.data.trucks.filter(t => t.active);
        const stale = new Set(staleTrucks().map(t => t.car_code));
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
            return h('tr', { class: stale.has(t.car_code) ? 'is-stale' : null },
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
            title: r.wear_source === 'garage_avg' ? 'Վերանորոգում չկա՝ հաշվարկում է մոդելի կամ ավտոպարկի միջինը'
                : 'Վերանորոգում չկա՝ հաշվարկում է կարգավորումների «մաշվածքը»' });
        return h('span', { class: 'rt-badge b-warn', text: 'Կուտակվում է՝ ' + g.months + ' / ' + g.ready_months + ' ամիս' });
    }

    function renderSummary() {
        const d = state.data, rules = d.rules;
        const avgLabel = (p) => p.scope === 'model' ? 'մոդելի միջին' + (p.model ? ' (' + p.model + ')' : '') : 'ավտոպարկի միջին';
        $('gjSumLead').textContent = 'Վերանորոգում ֏/կմ = վերանորոգման ծախսը ÷ վազքը նույն ժամանակահատվածում (բաշխված վերանորոգումից՝ '
            + 'այդ ամիսներին ընկնող մասը)։ Առաքման հաշվարկում է, երբ վազքը ծածկում է ' + rules.ready_months + ' ամիս և առնվազն '
            + fmt(rules.ready_km) + ' կմ, և այդ ընթացքում գրանցված է գոնե մեկ վերանորոգում։ Սեփական գինը մոտեցվում է նույն մոդելի '
            + '(կամ ամբողջ ավտոպարկի) միջինին՝ որքան քիչ կմ, այնքան ավելի (' + fmt(rules.blend_km) + ' կմ-ով)։ '
            + 'Մինչ այդ հաշվարկում է կարգավորումների «մաշվածքը», իսկ եթե այն դատարկ է՝ մոդելի (կամ ավտոպարկի) միջինը։';
        if (!d.summary.length) {
            $('gjSumRows').replaceChildren(h('tr', {}, h('td', { colspan: 8, class: 'rt-empty', text: 'Մեքենաներ չկան' })));
        } else {
            $('gjSumRows').replaceChildren(...d.summary.map(r => {
                const g = r.garage;
                const last = r.last_day
                    ? [h('span', { text: dayHy(r.last_day) }), h('small', { class: 'gj-sub', text: fmt(r.days_since_last) + ' օր առաջ' })]
                    : [];
                if (r.stale) last.push(h('span', { class: 'rt-badge b-warn gj-stale', text: 'Լրացրեք վազքը' }));
                // в расчёте — своя, сглаженная к средней модели (или парка): видно все три числа и что делится
                const price = g && g.price !== null
                    ? [h('b', { class: 'gj-price', text: fmt(g.price, 1) }),
                        g.blend ? h('small', { class: 'gj-sub', text: 'սեփական՝ ' + fmt(g.own, 1) }) : null,
                        g.blend ? h('small', { class: 'gj-sub', text: (g.blend === 'model' ? g.model + '-ի միջին՝ ' : 'ավտոպարկի միջին՝ ') + fmt(g.model_price, 1) }) : null,
                        h('small', { class: 'gj-sub', text: fmt(g.cost_amd) + ' ֏ ÷ ' + fmt(g.km) + ' կմ' })]
                    // своей цены нет, «Износ» в настройках пуст — в расчёте средняя модели (или парка)
                    : r.wear_source === 'garage_avg' ? [h('b', { class: 'gj-price', text: fmt(r.garage_prior.price, 1) }),
                        h('small', { class: 'gj-sub', text: avgLabel(r.garage_prior) }),
                        h('small', { class: 'gj-sub', text: 'սեփականը դեռ չկա, կարգավորումներում՝ դատարկ' })]
                    // ручное задано — средняя по журналу видна, но в расчёт не идёт
                    : r.wear_source === 'manual' && r.garage_prior ? [h('span', { text: '—' }),
                        h('small', { class: 'gj-sub', text: avgLabel(r.garage_prior) + '՝ ' + fmt(r.garage_prior.price, 1) }),
                        h('small', { class: 'gj-sub', text: 'չի կիրառվում, քանի որ կարգավորումներում արժեք կա' })]
                    : [h('span', { text: '—' })];
                // ремонт за 12 месяцев — полная сумма; «в расчёте» — с долями растянутых
                const repair = g ? [h('span', { text: fmt(g.repair_amd) }),
                    g.status !== 'none' && g.cost_amd !== g.repair_amd ? h('small', { class: 'gj-sub', text: 'հաշվարկում՝ ' + fmt(g.cost_amd) }) : null] : ['—'];
                return h('tr', { class: r.active ? null : 'is-closed' },
                    h('td', { class: 'rt-cell-name' }, h('span', { class: 'n', text: r.car_code }), h('span', { class: 'c', text: r.name || (r.active ? '' : 'չի աշխատում') })),
                    h('td', { class: 'gj-num w-half', 'data-label': 'Վերանորոգում, ֏' }, ...repair),
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

    // ---------- норма и факт ----------
    // GET /api/routes/garage/norm?month= — по машине: норма расхода (ручная) и расход по заправкам, км плана и GPS,
    // стоянки вне плана, точки не по порядку, по дням; GET /api/routes/garage/day?date=&car= — карта дня (как у
    // «Обучения»). Считается при первом открытии вкладки и при смене месяца — не при каждой загрузке страницы.
    const norm = { data: null, gen: 0, open: new Set(), timer: null, busy: false };
    const signed = (v, d = 0) => { const n = num(v); return n === null ? '—' : (n > 0 ? '+' : '') + fmt(n, d); };
    const FUEL_WHY = {
        no_refuels: () => 'ամսում լիցքավորում չկա',
        one_refuel: () => 'ամսում միայն մեկ լիցքավորում է — ծախսը հաշվվում է երկու լրիվ բաքի միջև',
        no_interval: (f) => fmt(f.refuels) + ' լիցքավորում, բայց լրիվ բաքից լրիվ բաք միջակայք չկա (լրիվ բաք, ճիշտ օդոմետր, առնվազն '
            + fmt(norm.data.rules.fuel_min_km) + ' կմ)',
        suspicious: () => 'բոլոր միջակայքերը կասկածելի են (տես կարմիր նշանը)',
    };
    // норма тревоги — ручная из настроек; выученная — рядом (она подогнана под те же заправки, что и факт)
    const NORM_SRC = { manual: 'կարգավորումներից', manual_profile: 'կարգավորումներից՝ դատարկ և լրիվ բեռնվածի միջինը',
        learned: 'սովորած՝ կարգավորումներում նորմ չկա, ահազանգը թույլ է' };
    const normVisible = () => !$('gjNorm').hidden && !document.hidden;
    const normRow = (text) => $('gjNormRows').replaceChildren(h('tr', {}, h('td', { colspan: 8, class: 'rt-empty', text })));

    // pending — факт месяца сервер досчитывает в фоне (впервые после перезапуска — до минуты): спросить снова через 3 с,
    // только пока вкладка видна и страница не в фоне; иначе — при возвращении (resumeNorm)
    async function loadNorm(again) {
        clearTimeout(norm.timer);
        norm.timer = null;
        const gen = again || ++norm.gen;
        const month = $('gjNormMonth').value;
        if (!again) { norm.data = null; normRow('Հաշվում է…'); }
        norm.busy = true;
        try {
            const d = await api('/api/routes/garage/norm' + (month ? '?month=' + encodeURIComponent(month) : ''));
            if (gen !== norm.gen) return;
            if (d.pending) {
                normRow('Հաշվում է ամսվա GPS հետագծերը… Առաջին անգամ սա կարող է տևել մինչև մեկ րոպե։');
                norm.timer = setTimeout(() => { norm.timer = null; if (gen === norm.gen && normVisible()) loadNorm(gen); }, 3000);
                return;
            }
            if (d.failed) { normRow('Չհաջողվեց հաշվել ամսվա GPS տվյալները (սերվերի սխալ)։ Բացեք ներդիրը նորից կամ կրկնեք ավելի ուշ։'); return; }
            norm.data = d;
            norm.open.clear();
            $('gjNormMonth').value = d.month;
            $('gjNormMonth').max = d.current_month;
            $('gjNormMonth').min = d.oldest_month;
            renderNorm();
        } catch (e) {
            if (gen !== norm.gen) return;
            showError(e.message);
            normRow('Չհաջողվեց բեռնել');
        } finally {
            if (gen === norm.gen) norm.busy = false;
        }
    }
    // вкладку открыли снова или страница вернулась из фона: данных нет и запроса нет — спросить (в т.ч. после pending)
    function resumeNorm() {
        if (normVisible() && !norm.data && !norm.busy && !norm.timer) loadNorm();
    }

    function fuelCell(t) {
        const f = t.fuel;
        if (f.l100 === null) {
            return [h('span', { class: 'gj-nodata', text: 'տվյալ չկա' }), h('small', { class: 'gj-sub', text: (FUEL_WHY[f.reason] || (() => ''))(f) })];
        }
        return [h('b', { class: 'gj-price' + (f.over ? ' gj-over' : ''), text: fmt(f.l100, 1) }),
            f.delta_pct !== null ? h('small', { class: 'gj-sub' + (f.over ? ' gj-over' : ''), text: 'նորմից՝ ' + signed(f.delta_pct, 1) + '%' }) : null,
            h('small', { class: 'gj-sub', text: fmt(f.liters, 1) + ' լ ÷ ' + fmt(f.km) + ' կմ · ' + fmt(f.intervals) + ' միջակայք' })];
    }

    function kmCell(t) {
        const k = t.km;
        if (!k.days) return [h('span', { class: 'gj-nodata', text: 'տվյալ չկա' }), h('small', { class: 'gj-sub', text: 'GPS հետագիծ չկա' })];
        if (!k.plan_days) return [h('span', { text: '—' }), h('small', { class: 'gj-sub', text: '«Առաքում» էջի պլան այս օրերին չկա' })];
        return [h('b', { class: 'gj-km' + (k.over ? ' gj-over' : ''), text: fmt(k.plan) + ' → ' + fmt(k.fact) }),
            h('small', { class: 'gj-sub' + (k.over ? ' gj-over' : ''), text: signed(k.delta) + ' կմ (' + signed(k.delta_pct, 1) + '%)' }),
            h('small', { class: 'gj-sub', text: 'պլանով օրեր՝ ' + fmt(k.plan_days) })];
    }

    const LITERS_NOTE = 'Օրվա լիտրերը մոտավոր են՝ օրվա GPS կմ × լիցքավորումների այն միջակայքի ծախսը, որի մեջ ընկնում է օրը։';
    function dayList(t) {
        return h('div', {}, h('p', { class: 'gj-sub gj-days-note', text: '≈ ' + LITERS_NOTE }),
            h('ul', { class: 'gj-days' }, ...t.days.map(d => h('li', {},
            h('b', { class: 'gj-day', text: dayHy(d.day) }),
            h('span', { class: d.over ? 'gj-over' : null, text: 'կմ՝ ' + (d.plan_km !== null ? fmt(d.plan_km) + ' → ' : 'պլան չկա → ') + fmt(d.fact_km) }),
            h('span', { title: LITERS_NOTE, text: 'լիտր՝ ' + (d.liters !== null ? '≈' + fmt(d.liters, 1) : '—') }),
            h('span', { text: 'պլանից դուրս՝ ' + fmt(d.unplanned_stays) }),
            h('span', { text: 'ոչ հերթականությամբ՝ ' + fmt(d.order_changes) }),
            h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-label': 'Քարտեզ՝ ' + t.car_code + ', ' + dayHy(d.day),
                dataset: { map: d.day, car: t.car_code } }, icon('fa-map-location-dot'), 'Քարտեզ')))));
    }

    function renderNorm() {
        const d = norm.data, pct = d.rules.alert_pct;
        $('gjNormLead').textContent = 'Նորմը՝ կարգավորումներում նշված ծախսը (եթե նշված են դատարկ և լրիվ բեռնվածի ծախսերը՝ դրանց միջինը)։ '
            + 'Ծրագրի սովորած ծախսը ցույց է տրվում կողքին․ այն հաշվված է նույն լիցքավորումներից, ուստի ահազանգի հիմք չէ։ Փաստը՝ վարորդների լիցքավորումներից՝ '
            + 'լրիվ բաքից լրիվ բաք․ լիտրերը ÷ կմ ըստ օդոմետրի (միջակայքը, որն անցնում է ամսվա սահմանով, մտնում է այն ամիսը, երբ '
            + 'ավարտվել է)։ Կմ-ը համեմատվում է միայն այն օրերին, երբ կա և «Առաքում» էջի պլանը, և GPS հետագիծը։ Կարմիրով է նշված '
            + 'այն, ինչ նորմից կամ պլանից ավելի է ' + pct + '%-ից ավելի։' + (d.month === d.current_month ? ' Այսօրը դեռ չի մտնում։' : '');
        $('gjNormEmpty').hidden = d.has_data;
        $('gjNormEmptyText').textContent = d.too_old
            ? 'Ցույց են տրվում միայն վերջին 12 ամիսները։'
            : !d.connected
            ? '«Առաքիչ» բաժինը միացված չէ — GPS հետագծեր և լիցքավորումներ չկան։'
            : 'Այս ամսում GPS հետագծեր և լիցքավորումներ դեռ չկան։ Դրանք կհայտնվեն, երբ վարորդներն աշխատեն «Առաքիչ» հավելվածի այն '
                + 'տարբերակով, որը գրանցում է GPS հետագիծը և լիցքավորումները (լիտր, օդոմետր, լրիվ բաք)։';
        if (!d.trucks.length) {
            $('gjNormRows').replaceChildren(h('tr', {}, h('td', { colspan: 8, class: 'rt-empty', text: 'Մեքենաներ չկան' })));
            return;
        }
        $('gjNormRows').replaceChildren(...d.trucks.flatMap(t => {
            const id = 'gjNormDays-' + t.car_code.replace(/[^\w-]/g, '_');
            const open = norm.open.has(t.car_code);
            const lim = d.rules.fuel_l100;
            const flags = [t.fuel.over ? h('span', { class: 'rt-badge b-danger', text: 'Վառելիք ' + signed(t.fuel.delta_pct, 1) + '%' }) : null,
                t.fuel.too_high ? h('span', { class: 'rt-badge b-danger', text: fmt(t.fuel.too_high) + ' կասկածելի լիցքավորում՝ ավելի քան ' + fmt(lim[1]) + ' լ/100 կմ' }) : null,
                t.fuel.too_low ? h('span', { class: 'rt-badge b-danger', text: fmt(t.fuel.too_low) + ' կասկածելի լիցքավորում՝ պակաս քան ' + fmt(lim[0]) + ' լ/100 կմ' }) : null,
                t.km.over ? h('span', { class: 'rt-badge b-danger', text: 'Կմ ' + signed(t.km.delta_pct, 1) + '%' }) : null].filter(Boolean);
            const row = h('tr', { class: (flags.length ? 'is-over' : '') + (t.active ? '' : ' is-closed') || null },
                h('td', { class: 'rt-cell-name' }, h('span', { class: 'n', text: t.car_code }), h('span', { class: 'c', text: t.name || (t.active ? '' : 'չի աշխատում') }),
                    flags.length ? h('span', { class: 'gj-flags' }, ...flags) : null),
                h('td', { class: 'gj-num w-half', 'data-label': 'Նորմ, լ/100 կմ' }, t.norm.l100 !== null
                    ? [h('b', { class: 'gj-price', text: fmt(t.norm.l100, 1) }),
                        h('small', { class: 'gj-sub' + (t.norm.source === 'learned' ? ' gj-weak' : ''), text: NORM_SRC[t.norm.source] || '' }),
                        t.norm.source !== 'learned' && t.norm.learned !== null ? h('small', { class: 'gj-sub', text: 'սովորած՝ ' + fmt(t.norm.learned, 1) }) : null]
                    : [h('span', { text: '—' }), h('small', { class: 'gj-sub', text: 'նշված չէ' })]),
                h('td', { class: 'gj-num w-half', 'data-label': 'Փաստ, լ/100 կմ' }, ...fuelCell(t)),
                h('td', { class: 'gj-num', 'data-label': 'Կմ՝ պլան → փաստ' }, ...kmCell(t)),
                h('td', { class: 'gj-num w-half', 'data-label': 'Կանգառներ պլանից դուրս', text: t.km.days ? fmt(t.unplanned_stays) : '—' }),
                h('td', { class: 'gj-num w-half', 'data-label': 'Ոչ հերթականությամբ', text: t.km.days ? fmt(t.order_changes) : '—' }),
                h('td', { class: 'gj-num w-half', 'data-label': 'Օրեր GPS-ով', text: fmt(t.km.days) }),
                h('td', { class: 'gj-acts' }, t.days.length ? h('button', { type: 'button', class: 'rt-btn rt-btn-ghost rt-btn-sm', 'aria-expanded': String(open),
                    'aria-controls': id, dataset: { days: t.car_code } }, icon(open ? 'fa-chevron-up' : 'fa-chevron-down'), 'Ըստ օրերի') : null));
            const more = h('tr', { id, class: 'gj-norm-days', hidden: !open }, h('td', { colspan: 8 }, open ? dayList(t) : null));
            return [row, more];
        }));
    }

    $('gjNormRows').addEventListener('click', (ev) => {
        const btn = ev.target.closest('button');
        if (!btn) return;
        if (btn.dataset.map) { showDay(btn.dataset.map, btn.dataset.car); return; }
        const car = btn.dataset.days;
        if (!car) return;
        if (norm.open.has(car)) norm.open.delete(car); else norm.open.add(car);
        renderNorm();
        const again = $('gjNormRows').querySelector('button[data-days="' + CSS.escape(car) + '"]');
        if (again) again.focus();
    });

    // карта дня: трек GPS поверх точек плана; плановые рейсы — по прямой от точки к точке (линии по дорогам — API
    // администратора); подложка — routes_basemap.js без ключа Яндекса: OpenStreetMap (страница открыта из интернета —
    // ключ ей не выдаётся)
    const YEREVAN = [40.1792, 44.4991];
    const map = { obj: null, layers: null, failed: false, gen: 0 };
    function ensureMap() {
        if (map.obj || map.failed) return;
        const el = $('gjMap');
        if (typeof window.L === 'undefined' || typeof window.RoutesBasemap === 'undefined') {   // CDN или подложка не загрузились
            map.failed = true;
            el.classList.add('rt-map-fallback');
            el.textContent = 'Քարտեզը չբեռնվեց։ Թարմացրեք էջը։';
            return;
        }
        map.obj = L.map(el, { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, zoomControl: false });
        L.control.zoom({ zoomInTitle: 'Մեծացնել', zoomOutTitle: 'Փոքրացնել' }).addTo(map.obj);
        RoutesBasemap.add(map.obj);
        map.obj.setView(YEREVAN, 11);
        map.layers = L.layerGroup().addTo(map.obj);
    }
    function stopColor(s) {
        if (!s.arrive) return '#8791a3';
        return (num(s.late_min) > 0 || s.early) ? '#ff6b79' : '#45d98f';
    }
    function stopTip(s) {
        const fact = !s.arrive ? 'կանգառ չի եղել'
            : s.arrive + '–' + (s.leave || '?') + (num(s.late_min) > 0 ? ', ուշացում՝ ' + fmt(s.late_min) + ' րոպե' : '') + (s.early ? ', ընդունման ժամից շուտ' : '');
        return h('span', {}, h('b', { text: s.name || String(s.customer_id || s.stop_id || '') }), h('br'),
            (num(s.rank) !== null ? '№' + fmt(s.rank + 1) + ' · ' : '') + 'պլան՝ ' + (s.planned_eta || '—') + ' · փաստ՝ ' + fact);
    }
    async function showDay(dayIso, car) {
        $('gjMapBox').hidden = false;
        $('gjMapTitle').textContent = truckName(car) + ' · ' + dayHy(dayIso);
        $('gjMapNote').textContent = 'Բեռնվում է…';
        $('gjMapBox').scrollIntoView({ behavior: 'smooth', block: 'start' });
        ensureMap();
        const gen = ++map.gen;
        let d;
        try { d = await api('/api/routes/garage/day?date=' + encodeURIComponent(dayIso) + '&car=' + encodeURIComponent(car)); }
        catch (e) { if (gen === map.gen) $('gjMapNote').textContent = e.message; return; }
        if (gen !== map.gen) return;
        const visited = d.stops.filter(s => s.arrive).length;
        $('gjMapNote').textContent = 'Շարժման կմ (GPS)՝ ' + fmt(d.km_gps, 1) + ' · կետեր՝ ' + fmt(visited) + ' / ' + fmt(d.stops.length)
            + (d.trips.length ? ' · երթեր՝ ' + d.trips.map((t, i) => (i + 1) + ') ' + (t.depart || '?') + '–' + (t.return || '?')).join(', ') : '')
            + (d.track.length > 1 ? '' : ' · GPS հետագիծ այս օրը չկա');
        if (!map.obj) return;
        map.obj.invalidateSize();
        map.layers.clearLayers();
        const bounds = [];
        d.planned.forEach(line => {
            bounds.push(...line);
            L.polyline(line, { color: '#8791a3', weight: 3, opacity: .85, dashArray: '6 6' }).addTo(map.layers);
        });
        if (d.track.length > 1) {
            L.polyline(d.track, { color: '#3b82f6', weight: 3, opacity: .85 }).addTo(map.layers);
            bounds.push(...d.track);
        }
        d.stops.forEach(s => {
            if (s.lat === null) return;
            bounds.push([s.lat, s.lon]);
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0c0f14', weight: 2, fillColor: stopColor(s), fillOpacity: 1 })
                .bindTooltip(stopTip(s)).addTo(map.layers);
        });
        if (d.depot) {
            bounds.push(d.depot);
            L.marker(d.depot, { icon: L.divIcon({ className: 'rt-pin rt-pin-depot', html: '<span><i class="fas fa-warehouse" aria-hidden="true"></i></span>', iconSize: [28, 28], iconAnchor: [14, 14] }), keyboard: false, zIndexOffset: 1000 })
                .bindTooltip('Պահեստ').addTo(map.layers);
        }
        if (bounds.length) map.obj.fitBounds(bounds, { padding: [24, 24], maxZoom: 15, animate: false });
    }
    $('gjMapClose').addEventListener('click', () => { $('gjMapBox').hidden = true; map.gen += 1; });

    // ---------- загрузка ----------
    async function reload() {
        state.data = await api('/api/routes/garage');
        fillCars();
        renderOdo();
        renderSummary();
        renderBanner();
        await loadEntries();
    }

    async function init() {
        let tab = 'gjTabCosts';
        try { tab = localStorage.getItem('gjTab') || tab; } catch (e) { /* без памяти вкладки */ }
        selectTab(TABS.some(([t]) => t === tab) ? tab : 'gjTabCosts');
        $('gjForm').addEventListener('submit', saveEntry);
        $('gjCancel').addEventListener('click', resetForm);
        $('gjKind').addEventListener('change', () => syncKind(true));
        $('gjWhat').addEventListener('change', () => {
            syncWhat(true);
            if ($('gjWhat').value === OTHER) $('gjWhatOther').focus();
        });
        $('gjAmount').addEventListener('input', syncSpread);
        $('gjSpread').addEventListener('change', () => { state.spreadTouched = true; state.spreadAuto = false; syncSpread(); });
        $('gjBannerGo').addEventListener('click', goStale);
        $('gjCar').addEventListener('change', syncOdoHint);
        $('gjOdoForm').addEventListener('submit', saveOdo);
        const refilter = () => loadEntries().catch(e => showError(e.message));
        $('gjFilterCar').addEventListener('change', refilter);
        $('gjFilterMonth').addEventListener('change', refilter);
        if ($('gjShowDeleted')) $('gjShowDeleted').addEventListener('change', refilter);
        $('gjNormMonth').addEventListener('change', () => { if ($('gjNormMonth').value) loadNorm(); });
        document.addEventListener('visibilitychange', resumeNorm);
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

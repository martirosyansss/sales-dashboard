/* «Աշխատավարձ» /routes/pay — зарплата առաքիչ за месяц (docs/research/09-crew-pay.md, формула владельца 07.10).
   API: GET /api/routes/pay?month=YYYY-MM (люди, итог, D, параметры, месяцы), GET /api/routes/pay.csv?month= (Excel),
   GET /api/routes/pay/params (параметры без ERP: форма работает и при недоступной ERP), POST /api/routes/pay/params
   (сохранить; ошибки — по полям). Только администратору.
   Всё, что пришло с сервера, выводится только через textContent. CSRF-заголовок к fetch добавляет base_v2.html. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const fmt = (v, d = 0) => (v === null || v === undefined || !Number.isFinite(Number(v))) ? '—'
        : Number(v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
    const amd = (v) => fmt(v) + ' ֏';
    const signed = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmt(Math.abs(v));
    const dayHy = (iso) => iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4);
    const MONTHS = ['հունվար', 'փետրվար', 'մարտ', 'ապրիլ', 'մայիս', 'հունիս', 'հուլիս', 'օգոստոս', 'սեպտեմբեր', 'հոկտեմբեր', 'նոյեմբեր', 'դեկտեմբեր'];
    const monthHy = (key) => MONTHS[+key.slice(5, 7) - 1] + ' ' + key.slice(0, 4);

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
            else el.setAttribute(k, String(v));
        });
        kids.flat().forEach(c => {
            if (c === null || c === undefined || c === false) return;
            el.append(c instanceof Node ? c : document.createTextNode(String(c)));
        });
        return el;
    }

    // paramsLoaded — форма параметров заполнена из GET /api/routes/pay/params (до этого «Պահպանել» выключена); правки в
    // форме при смене месяца не затираются: форма заполняется только при открытии страницы и после сохранения
    const state = { data: null, month: '', open: null, seq: 0, paramsLoaded: false };

    function announce(text) { $('cpStatus').textContent = ''; setTimeout(() => { $('cpStatus').textContent = text; }, 30); }
    function showError(text) { $('cpAlert').hidden = !text; $('cpAlertText').textContent = text || ''; }

    class ApiError extends Error {
        constructor(message, errors) { super(message); this.errors = errors || {}; }
    }
    // Ответы сервера по-армянски: проверки параметров уже армянские (их и показываем, по полям); прочее — своим текстом по коду
    const HY = /[Ա-֏]/;
    function httpError(status, body) {
        const text = body && typeof body.error === 'string' ? body.error : '';
        if (status === 403) return text === 'csrf' ? 'Էջը հնացել է՝ թարմացրեք այն և կրկնեք։' : 'Այս էջը ձեզ թույլատրված չէ։';
        if (status === 400 && HY.test(text)) return text;
        if (status === 400 || status === 415) return 'Հարցումը չընդունվեց։ Թարմացրեք էջը և կրկնեք։';
        if (status === 503) return 'ERP տվյալների բազան հասանելի չէ, աշխատավարձը հնարավոր չէ հաշվել։ Կրկնեք մի փոքր ուշ։';
        if (status === 500 && HY.test(text)) return text;
        return 'Սերվերի սխալ (' + status + ')։ Կրկնեք մի փոքր ուշ։';
    }
    async function api(url, json) {
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (json !== undefined) { init.method = 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(json); }
        let resp, body = null;
        try { resp = await fetch(url, init); } catch (e) { throw new ApiError('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
        if (resp.status === 401) {
            window.location.assign('/login?next=' + encodeURIComponent('/routes/pay'));
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

    // ---------- загрузка месяца ----------
    async function load(month) {
        const seq = ++state.seq;          // ответ на прежний выбор месяца не перетирает новый
        showError('');
        $('cpLoading').hidden = false;
        $('cpTableWrap').hidden = true;
        $('cpEmpty').hidden = true;
        closeDays();
        try {
            const data = await api('/api/routes/pay' + (month ? '?month=' + encodeURIComponent(month) : ''));
            if (seq !== state.seq) return;
            state.data = data;
            state.month = data.month;
            render();
            announce('Աշխատավարձը հաշվված է՝ ' + monthHy(data.month));
        } catch (e) {
            if (seq !== state.seq) return;
            state.data = null;                // ERP недоступна — ошибка, а не нули
            $('cpSub').textContent = '';
            $('cpFormula').replaceChildren();
            showError(e.message);
        } finally {
            if (seq === state.seq) $('cpLoading').hidden = true;
        }
    }

    function render() {
        const d = state.data;
        renderMonths(d.months, d.month);
        // D — дни с доставкой (в текущем месяце — по сегодня); D_month — знаменатель фикса и нормы: у прошлого месяца = D,
        // у текущего — дни с доставкой по сегодня ∪ рабочие дни календаря с сегодня до конца месяца
        const dm = d.workdays_month ?? d.workdays;
        const daysText = d.current ? d.workdays + ' անցած / ' + dm + ' ամսում' : String(d.workdays);
        $('cpSub').textContent = monthHy(d.month) + (d.current ? ' (մինչև այսօր)' : '') + ' · աշխատանքային օրեր՝ ' + daysText;
        $('cpOldHead').textContent = 'Հին սխեմա (' + fmt(d.params.old_pct, d.params.old_pct % 1 ? 1 : 0) + '%)';
        const warn = [
            d.unknown_codes.length ? 'ERP-ում չկան այս կոդերը՝ ' + d.unknown_codes.join(', ') + '։ Ստուգեք պարամետրերը։' : '',
            ...d.overlapping_codes.map(c => 'Նույն անունով կոդեր, որոնցից մի քանիսը աշխատել են նույն օրերին՝ '
                + c.join(', ') + ' — ստուգեք։ Հաշվված են առանձին։'),
            d.calendar_warning || '',
            d.excluded_kin.length ? 'Այս կոդերը հաշվվում են, բայց նույն անունով կոդ կա չհաշվվողների մեջ՝ '
                + d.excluded_kin.join(', ') + ' — նշեք մարդու բոլոր կոդերը։' : '',
            ...(d.km_warnings || []),
        ].filter(Boolean);
        $('cpWarn').hidden = !warn.length;
        $('cpWarnText').textContent = warn.join(' ');
        renderFormula(d.params, dm, d.current ? daysText : '', d.km_counted);
        renderTable(d);
    }

    // Месяцы: с сервера, а до первого ответа (или если ERP недоступна) — 12 последних по часам компьютера
    function localMonths() {
        const now = new Date();
        return Array.from({ length: 12 }, (_, i) => {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
        });
    }
    function renderMonths(months, current) {
        $('cpMonth').replaceChildren(...months.map((key, i) => h('option', {
            value: key, text: monthHy(key) + (i === 0 ? ' (մինչև այսօր)' : ''),
        })));
        $('cpMonth').value = current || months[0];
    }

    function renderFormula(p, D, currentDays, kmCounted) {
        const n = (text) => h('span', { class: 'num', text });
        const dText = D ? String(D) : '—';
        $('cpFormula').replaceChildren(
            h('p', null, h('b', { text: 'Վճարել' }), ' = ամենամեծը երկուսից՝ (Ֆիքս + Գործավարձ) կամ Նվազագույն'),
            h('p', null, h('b', { text: 'Ֆիքս' }), ' = ', n(amd(p.fix)), ' × (աշխատած օրեր ÷ ', n(dText), ' աշխատանքային օր)'),
            h('p', null, h('b', { text: 'Գործավարձ' }), ' = ', n(amd(p.rate_point)), ' × կետեր + ', n(amd(p.rate_tonne)), ' × տոննա',
                p.rate_km > 0 ? [' + ', n(amd(p.rate_km)), ' × կմ'] : ''),
            p.rate_km > 0 ? h('p', null, h('b', { text: 'Կմ' }), ' = պլանային կմ, ոչ թե փաստացի (GPS կամ սպիդոմետր)՝ ամեն օր '
                + 'պահեստ → այդ օրվա խանութները → պահեստ, կարճ հերթականությամբ (մոտավոր)՝ ճանապարհներով։ Օրը մեկ երթ է՝ առանց '
                + 'բաժանելու ռեյսերի ըստ տոննաժի։ Խանութը վերցվում է իր հիմնական առաքման հասցեի կետով (ճշտված կետով, եթե այն ճշտվել է)։ '
                + 'Կոորդինատներ չունեցող խանութը կմ չի ավելացնում։'
                + (kmCounted ? '' : ' Այս ամսի կմ-ն հաշվված չէ (տե՛ս նախազգուշացումը)։')) : '',
            h('p', null, h('b', { text: 'Նվազագույն' }), ' = ', n(amd(p.minimum)), ' × (կետեր ÷ նորմ), բայց ոչ ավելի, քան ',
                n(amd(p.minimum)), '։ Նորմ = ', n(fmt(p.norm_per_day, p.norm_per_day % 1 ? 1 : 0)), ' կետ × ', n(dText), ' օր',
                D ? [' = ', n(fmt(p.norm_per_day * D, (p.norm_per_day * D) % 1 ? 1 : 0)), ' կետ'] : ''),
            h('p', null, h('b', { text: 'Հին սխեմա' }), ' (համեմատության համար) = ', n(amd(p.old_fix)),
                ' × (աշխատած օրեր ÷ ', n(dText), ') + վաճառքի ', n(fmt(p.old_pct, p.old_pct % 1 ? 1 : 0) + '%')),
            h('p', { class: 'note', text: 'Աշխատանքային օրեր՝ անցած ամսում՝ այն օրերը, երբ որևէ առաքիչ առաքում է արել, '
                + 'իսկ ընթացիկ ամսում՝ մինչև այսօր առաքում ունեցած օրերը գումարած ամսվա մնացած աշխատանքային օրերը ըստ '
                + 'կարգավորումների օրացույցի (աշխատանքային օրեր և ոչ աշխատանքային ամսաթվեր)։ Կետ՝ խանութ, '
                + 'որին այդ օրը ապրանք է հասցվել (մեկ օրում մեկ խանութին մի քանի ապրանքագիր = 1 կետ)։ Տոննա՝ ապրանքի քաշն ըստ '
                + 'ERP-ի։ Վերադարձները դեռ չեն հանվում, իսկ միայն զրոյական կամ մինուսային ապրանքագրերը '
                + 'կետ և աշխատանքային օր չեն համարվում։' }),
            ...(currentDays ? [h('p', { class: 'note' }, 'Ընթացիկ ամիս՝ աշխատանքային օրեր ', n(currentDays), '։ Ֆիքսը և '
                + 'նորմը հաշվվում են ամբողջ ամսվա ', n(dText), ' աշխատանքային օրից, այնպես որ ցույց է տրված մինչև այսօր '
                + 'հաշվեգրվածը։')] : []),
        );
    }

    function renderTable(d) {
        const rows = d.rows;
        $('cpEmpty').hidden = rows.length > 0;
        $('cpTableWrap').hidden = rows.length === 0;
        const num = (text, cls) => h('td', { class: 'num' + (cls ? ' ' + cls : ''), text });
        $('cpRows').replaceChildren(...rows.map((r, i) => {
            const tr = h('tr', { 'data-i': i },
                h('td', { class: 'txt' }, r.code,
                    r.agent_ids.length > 1 ? h('span', { class: 'rt-badge b-none', text: r.agent_ids.length + ' կոդ' }) : null),
                h('td', { class: 'txt' }, h('button', { type: 'button', class: 'rt-linkbtn', text: r.name || r.code,
                    'aria-label': (r.name || r.code) + '՝ օր առ օր' })),
                num(r.days + '/' + (d.workdays_month ?? d.workdays)),
                num(fmt(r.points)),
                num(fmt(r.tonnes, 1)),
                h('td', { class: 'num' }, d.km_counted ? fmt(r.km) : '—', r.no_coords ? h('span', {
                    class: 'rt-badge b-warn', title: r.no_coords + ' կետ առանց կոորդինատների — կմ-ն պակաս է հաշվված',
                    'aria-label': r.no_coords + ' կետ առանց կոորդինատների' },
                    h('i', { class: 'fas fa-location-crosshairs', 'aria-hidden': 'true' }), ' ' + r.no_coords) : null),
                num(fmt(r.fix)),
                num(fmt(r.piece)),
                num(fmt(r.minimum), r.min_applied ? 'is-min' : ''),
                h('td', { class: 'num pay' }, fmt(r.pay), r.min_applied ? h('span', { class: 'rt-badge b-warn', text: 'նվազագույն' }) : null),
                num(fmt(r.old)),
                num(signed(r.diff)),
            );
            tr.addEventListener('click', () => openDays(i));
            return tr;
        }));
        const t = d.totals;
        $('cpTotals').replaceChildren(rows.length ? h('tr', null,
            h('td', { colspan: 2, text: 'Ընդամենը' }), num(''), num(fmt(t.points)), num(fmt(t.tonnes, 1)),
            num(d.km_counted ? fmt(t.km) : '—'), num(fmt(t.fix)),
            num(fmt(t.piece)), num(''), num(fmt(t.pay), 'pay'), num(fmt(t.old)), num(signed(t.diff))) : '');
    }

    // ---------- день за днём ----------
    function openDays(i) {
        const r = state.data.rows[i];
        state.open = i;
        document.querySelectorAll('#cpRows tr').forEach(tr => tr.classList.toggle('is-open', +tr.dataset.i === i));
        $('cpDaysTitle').replaceChildren(h('i', { class: 'fas fa-calendar-days', 'aria-hidden': 'true' }),
            (r.name || r.code) + ' · ' + r.code + ' · ' + monthHy(state.month));
        $('cpDayRows').replaceChildren(...r.by_day.map(x => h('tr', null,
            h('td', { class: 'txt', text: dayHy(x.date) }), h('td', { class: 'num', text: fmt(x.points) }),
            h('td', { class: 'num', text: fmt(x.tonnes, 2) }),
            h('td', { class: 'num' }, state.data.km_counted ? fmt(x.km, 1) : '—', x.no_coords ? h('span', {
                class: 'rt-badge b-warn', title: x.no_coords + ' կետ առանց կոորդինատների',
                'aria-label': x.no_coords + ' կետ առանց կոորդինատների' },
                h('i', { class: 'fas fa-location-crosshairs', 'aria-hidden': 'true' }), ' ' + x.no_coords) : null),
            h('td', { class: 'num', text: fmt(x.piece) }))));
        $('cpDays').hidden = false;
        $('cpDays').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
        $('cpDays').focus({ preventScroll: true });
    }
    function closeDays() {
        const back = state.open;
        state.open = null;
        $('cpDays').hidden = true;
        document.querySelectorAll('#cpRows tr.is-open').forEach(tr => tr.classList.remove('is-open'));
        return back;
    }
    $('cpDaysClose').addEventListener('click', () => {
        const i = closeDays();
        const btn = i === null ? null : document.querySelector('#cpRows tr[data-i="' + i + '"] button');
        if (btn) btn.focus();
    });

    // ---------- параметры ----------
    const NUM_FIELDS = { fix: 'cpFix', rate_point: 'cpRatePoint', rate_tonne: 'cpRateTonne', rate_km: 'cpRateKm', minimum: 'cpMin',
        norm_per_day: 'cpNorm', old_fix: 'cpOldFix', old_pct: 'cpOldPct' };
    const CODE_FIELDS = { excluded_lines: 'cpLines', excluded_people: 'cpPeople' };

    // body — ответ GET/POST /api/routes/pay/params: параметры, кто/когда сохранил, битая ли запись в базе
    function fillParams(body) {
        const p = body.params;
        Object.entries(NUM_FIELDS).forEach(([k, id]) => { $(id).value = String(p[k]); });
        Object.entries(CODE_FIELDS).forEach(([k, id]) => { $(id).value = p[k].join(', '); });
        state.paramsLoaded = true;
        $('cpSave').disabled = false;
        $('cpChanged').textContent = body.store_error
            ? 'Պահպանված պարամետրերը վնասված են․ ցույց են տրված լռելյայն արժեքները — ստուգեք և պահպանեք։'
            : body.updated_at
                ? 'Պարամետրերը փոխվել են՝ ' + dayHy(body.updated_at) + ' ' + body.updated_at.slice(11, 16)
                  + (body.updated_by ? ', ' + body.updated_by : '') + '։ Նոր պարամետրերը կիրառվում են նաև նախորդ ամիսների վրա։'
                : 'Պարամետրերը լռելյայն են (դեռ չեն փոխվել)։';
    }
    async function loadParams() {
        try {
            fillParams(await api('/api/routes/pay/params'));
        } catch (e) {
            $('cpChanged').textContent = 'Պարամետրերը չբեռնվեցին՝ ' + e.message;
        }
    }
    function fieldErrors(errors) {
        document.querySelectorAll('#cpParams .rt-ferr').forEach(el => {
            const text = errors[el.dataset.for] || '';
            el.textContent = text;
            const input = $(NUM_FIELDS[el.dataset.for] || CODE_FIELDS[el.dataset.for]);
            input.classList.toggle('is-invalid', !!text);
            if (text) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
        });
    }

    $('cpParams').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const body = {};
        Object.entries(NUM_FIELDS).forEach(([k, id]) => {
            const raw = $(id).value.trim();
            body[k] = raw === '' || !Number.isFinite(Number(raw)) ? null : Number(raw);
        });
        Object.entries(CODE_FIELDS).forEach(([k, id]) => { body[k] = $(id).value; });
        $('cpSave').disabled = true;
        $('cpSaved').textContent = '';
        fieldErrors({});
        try {
            fillParams(await api('/api/routes/pay/params', body));
            $('cpSaved').textContent = 'Պարամետրերը պահպանվեցին';
            await load(state.month);
        } catch (e) {
            fieldErrors(e.errors);
            const first = Object.keys(e.errors)[0];
            if (first && (NUM_FIELDS[first] || CODE_FIELDS[first])) $(NUM_FIELDS[first] || CODE_FIELDS[first]).focus();
            else showError(e.message);
        } finally {
            $('cpSave').disabled = !state.paramsLoaded;
        }
    });

    // CSV: сначала запрос, потом файл — при недоступной ERP показываем ошибку, а не скачиваем JSON
    $('cpCsv').addEventListener('click', async () => {
        const month = $('cpMonth').value;
        $('cpCsv').disabled = true;
        showError('');
        try {
            let resp;
            try {
                resp = await fetch('/api/routes/pay.csv?month=' + encodeURIComponent(month), { credentials: 'same-origin', cache: 'no-store' });
            } catch (e) { throw new ApiError('Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկնեք։'); }
            if (resp.status === 401) {   // сессия кончилась — на вход, как api()
                window.location.assign('/login?next=' + encodeURIComponent('/routes/pay'));
                throw new ApiError('Մուտք գործեք նորից։');
            }
            if (!resp.ok) {
                let body = null;
                try { body = await resp.json(); } catch (e) { /* не JSON */ }
                throw new ApiError(httpError(resp.status, body));
            }
            const url = URL.createObjectURL(await resp.blob());
            const a = h('a', { href: url, download: 'crew-pay-' + month + '.csv' });
            document.body.append(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            showError(e.message);
        } finally {
            $('cpCsv').disabled = false;
        }
    });

    $('cpMonth').addEventListener('change', () => load($('cpMonth').value));
    renderMonths(localMonths(), '');
    loadParams();
    load('');
})();

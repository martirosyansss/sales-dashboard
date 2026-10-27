/* Read-only refueling journal. Text from drivers is always inserted as text. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const fmt = (v, digits = 0) => v === null || v === undefined ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: digits });
    const day = s => typeof s === 'string' && s.length >= 10 ? s.slice(8, 10) + '.' + s.slice(5, 7) + '.' + s.slice(0, 4) : '—';
    const API = '/api/routes/garage/refuels';
    const state = { page: 1, pages: 1, query: null, controller: null, serial: 0 };
    const FLAGS = { odometer_suspicious: 'Ստուգեք օդոմետրի ցուցմունքը', date_suspicious: 'Ստուգեք ամսաթիվը', clock_skew: 'Սարքի ժամացույցի շեղում' };
    function node(tag, text, cls) {
        const el = document.createElement(tag);
        if (text !== null && text !== undefined) el.textContent = text;
        if (cls) el.className = cls;
        return el;
    }
    function cell(label, children, cls) {
        const td = node('td', null, cls), box = node('div');
        td.dataset.label = label;
        children.forEach(child => box.append(child));
        td.append(box);
        return td;
    }
    function row(r) {
        const tr = node('tr', null, r.superseded ? 'is-corrected' : '');
        const notes = [];
        if (r.place) notes.push(node('small', r.place));
        if (r.superseded) notes.push(node('span', 'Ուղղված հին գրառում · չի գումարվում', 'rf-flag'));
        else if (r.correction) notes.push(node('small', 'Ուղղված տվյալներ'));
        r.flags.forEach(f => notes.push(node('span', FLAGS[f] || 'Պահանջում է ստուգում', 'rf-flag')));
        const photos = node('div', null, 'rf-photos');
        r.photos.forEach((p, i) => {
            if (!/^\/api\/routes\/garage\/refuels\/photos\/[0-9a-f-]{36}$/.test(p.url)) return;
            const a = node('a', null, 'rf-photo'), img = node('img');
            a.href = p.url; a.target = '_blank'; a.rel = 'noopener';
            a.setAttribute('aria-label', 'Լուսանկար ' + (i + 1) + '՝ ' + r.car_code + ', ' + day(r.day));
            img.src = p.url; img.alt = ''; img.loading = 'lazy';
            a.append(img, document.createTextNode('Բացել')); photos.append(a);
        });
        if (photos.childElementCount) notes.push(photos);
        if (!notes.length) notes.push(node('small', '—'));
        tr.append(cell('Ամսաթիվ / ժամ', [node('strong', day(r.day)), node('small', r.at ? r.at.slice(11, 16) : '—')]),
            cell('Մեքենա / վարորդ', [node('strong', r.car_code), node('small', r.driver_name || '—')]),
            cell('Լիտր', [node('strong', fmt(r.liters, 2))], 'rf-number'),
            cell('Օդոմետր, կմ', [node('span', fmt(r.odometer_km))], 'rf-number'),
            cell('Լրիվ բաք', [node('span', r.full_tank ? 'Այո' : 'Ոչ')]),
            cell('Գումար, ֏', [node('strong', fmt(r.amount_amd, 2))], 'rf-number'),
            cell('Նշում / լուսանկար', notes, 'rf-meta'));
        return tr;
    }
    function render(d) {
        const selected = d.car;
        $('rfCar').replaceChildren(new Option('Բոլոր մեքենաները', ''), ...d.cars.map(c => new Option(c, c)));
        if (selected && !d.cars.includes(selected)) $('rfCar').add(new Option(selected, selected));
        $('rfCar').value = selected;
        $('rfSince').value = d.since; $('rfUntil').value = d.until; $('rfCorrected').checked = d.corrected;
        $('rfCount').textContent = fmt(d.totals.count);
        $('rfCars').textContent = fmt(d.totals.cars) + ' մեքենա';
        $('rfLiters').textContent = fmt(d.totals.liters, 2);
        $('rfAmount').textContent = fmt(d.totals.amount_amd, 2);
        $('rfMissing').textContent = d.totals.missing_amount ? fmt(d.totals.missing_amount) + ' գրառման գումար նշված չէ' : 'Գրանցված գումարների հանրագումար';
        $('rfPeriod').textContent = day(d.since) + ' – ' + day(d.until);
        $('rfRows').replaceChildren(...d.entries.map(row));
        $('rfEmpty').hidden = d.total > 0; $('rfTableBox').hidden = d.total === 0;
        $('rfPagination').hidden = d.pages <= 1;
        $('rfPageLabel').textContent = fmt(d.page) + ' / ' + fmt(d.pages);
        $('rfPrev').disabled = d.page <= 1; $('rfNext').disabled = d.page >= d.pages;
        state.page = d.page; state.pages = d.pages;
        $('rfResults').hidden = false;
        $('rfStatus').textContent = d.total ? fmt(d.total) + ' գրառում · թարմացված է' : 'Ցուցակը թարմացված է';
        const query = new URLSearchParams({ since: d.since, until: d.until });
        if (d.car) query.set('car', d.car);
        if (d.corrected) query.set('corrected', '1');
        if (d.page > 1) query.set('page', d.page);
        history.replaceState(null, '', '/routes/refuels?' + query);
    }
    async function load(query) {
        if (state.controller) state.controller.abort();
        const controller = new AbortController(), serial = ++state.serial;
        state.controller = controller;
        $('rfResults').hidden = true; $('rfError').hidden = true;
        $('rfStatus').textContent = 'Բեռնվում է…'; $('rfPage').setAttribute('aria-busy', 'true');
        $('rfFilters').querySelectorAll('input, select, button').forEach(el => { el.disabled = true; });
        try {
            const response = await fetch(API + '?' + query, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal });
            if (serial !== state.serial) return;
            if (response.status === 401 || response.redirected && response.url.includes('/login')) {
                window.location.assign('/login?next=' + encodeURIComponent(location.pathname + location.search));
                return;
            }
            let d;
            try { d = await response.json(); } catch (_) { throw new Error('Տվյալները չհաջողվեց բեռնել։ Կրկին փորձեք։'); }
            if (!response.ok || !d.success) {
                if (response.status === 403) throw new Error('Այս բաժինը ձեզ հասանելի չէ։');
                throw new Error(d.error && /[\u0531-\u058f]/.test(d.error) ? d.error : 'Տվյալները չհաջողվեց բեռնել։ Կրկին փորձեք։');
            }
            if (serial !== state.serial) return;
            state.query = new URLSearchParams(query);
            render(d);
        } catch (e) {
            if (e.name === 'AbortError' || serial !== state.serial) return;
            $('rfErrorText').textContent = e instanceof TypeError ? 'Սերվերը հասանելի չէ։ Ստուգեք կապը և կրկին փորձեք։' : e.message;
            $('rfError').hidden = false; $('rfStatus').textContent = 'Չհաջողվեց թարմացնել ցուցակը։';
        } finally {
            if (serial === state.serial) {
                $('rfPage').removeAttribute('aria-busy');
                $('rfFilters').querySelectorAll('input, select, button').forEach(el => { el.disabled = false; });
            }
        }
    }
    $('rfFilters').addEventListener('submit', e => {
        e.preventDefault();
        if ($('rfSince').value > $('rfUntil').value) {
            $('rfErrorText').textContent = 'Սկզբի ամսաթիվը պետք է լինի ավարտի ամսաթվից առաջ։';
            $('rfError').hidden = false; $('rfResults').hidden = true; return;
        }
        load(new URLSearchParams({ since: $('rfSince').value, until: $('rfUntil').value, car: $('rfCar').value, corrected: $('rfCorrected').checked ? '1' : '0' }));
    });
    function paginate(step) {
        if (!state.query) return;
        const query = new URLSearchParams(state.query); query.set('page', String(state.page + step)); load(query);
    }
    $('rfPrev').addEventListener('click', () => paginate(-1));
    $('rfNext').addEventListener('click', () => paginate(1));
    const params = new URLSearchParams(location.search), initial = new URLSearchParams();
    ['since', 'until', 'car', 'corrected', 'page'].forEach(key => { if (params.has(key)) initial.set(key, params.get(key)); });
    load(initial);
})();

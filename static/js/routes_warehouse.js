/* «Պահեստ» /routes/warehouse — склад отмечает погрузку рейсов с телефона (ответ владельца №78).
   API: GET /api/routes/warehouse?date= (сегодня или следующий рабочий день: машины и рейсы утверждённого плана),
   GET /api/routes/warehouse/goods?date=&truck=&trip=&rev= (товар рейса), POST /api/routes/warehouse/loaded
   {date, rev, trip, loaded}, GET /api/routes/warehouse/waybill?date=&truck=&rev= (Բեռնագիր машины — тот же лист, что у
   «Развоза»: рендер window.RtWaybill из base.js). rev — номер плана на странице: план изменился — 409, страница просит
   обновить. Ответы сервера — по-армянски; всё с сервера выводится только через textContent (лист Բեռնագիր — через esc
   рендера). CSRF к fetch добавляет base_v2.html. */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const fmt = (v) => (Number.isFinite(Number(v)) ? Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : '—');
    const NB = ' ';
    const WEEKDAYS = ['Կիրակի', 'Երկուշաբթի', 'Երեքշաբթի', 'Չորեքշաբթի', 'Հինգշաբթի', 'Ուրբաթ', 'Շաբաթ'];
    const REFRESH_MS = 60000;
    const HY = /[Ա-֏]/;

    function h(tag, props, ...kids) {
        const el = document.createElement(tag);
        Object.entries(props || {}).forEach(([k, v]) => {
            if (v === null || v === undefined || v === false) return;
            if (k === 'class') el.className = v;
            else if (k === 'text') el.textContent = v;
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

    const state = { day: null, data: null, busy: false, stale: false };

    function announce(text) { $('whStatus').textContent = ''; setTimeout(() => { $('whStatus').textContent = text; }, 30); }
    function showAlert(text, reload) {
        $('whAlert').hidden = !text;
        $('whAlertText').textContent = text || '';
        $('whReload').hidden = !reload;
    }

    // Ошибка сервера: армянский текст — как есть; прочее (вход, доступ, сбой) — свой текст по коду
    function errorText(status, body) {
        const text = body && typeof body.error === 'string' ? body.error : '';
        if (text && HY.test(text)) return text;
        if (status === 401) return 'Մուտքի ժամկետն ավարտվել է՝ մուտք գործեք նորից։';
        if (status === 403) return text === 'csrf' ? 'Էջը հնացել է՝ թարմացրեք այն։' : 'Այս գործողությունը ձեզ թույլատրված չէ։';
        if (status === 409) return 'Պլանը փոխվել է — թարմացրեք էջը';
        if (status === 0) return 'Սերվերի հետ կապ չկա․ ստուգեք ինտերնետը։';
        if (status === 503) return 'ERP-ն հասանելի չէ — փորձեք մի փոքր ուշ։';
        return 'Սերվերի սխալ (կոդ ' + status + ')։';
    }

    async function api(method, url, body) {
        const opts = { method, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        let resp, data = null;
        try { resp = await fetch(url, opts); } catch (e) { throw Object.assign(new Error(errorText(0)), { status: 0 }); }
        try { data = await resp.json(); } catch (e) { data = null; }
        if (resp.ok && data && data.success === true) return data;
        if (resp.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); }
        throw Object.assign(new Error(errorText(resp.status, data)), { status: resp.status });
    }

    // «06.10» и подпись дня: сегодня / завтра / день недели
    const dm = (iso) => iso.slice(8, 10) + '.' + iso.slice(5, 7);
    function dayLabel(iso, k, days) {
        if (k === 0) return 'Այսօր';
        const d = new Date(iso + 'T12:00:00'), t = new Date(days[0] + 'T12:00:00');
        return (d - t) / 86400000 === 1 ? 'Վաղը' : WEEKDAYS[d.getDay()];
    }

    function renderDays() {
        const box = $('whDays');
        box.textContent = '';
        const days = state.data ? state.data.days : [];
        days.forEach((iso, k) => {
            const b = h('button', { type: 'button', role: 'tab', 'aria-selected': iso === state.day ? 'true' : 'false' },
                dayLabel(iso, k, days), h('small', { text: dm(iso) }));
            b.addEventListener('click', () => { if (iso !== state.day && !state.busy) load(iso); });
            box.append(b);
        });
    }

    function goods(d, truck, tr) {
        const box = h('details', { class: 'wh-goods' }, h('summary', null, icon('fa-boxes-stacked'), 'Ապրանքները'));
        const body = h('div', null);
        box.append(body);
        box.addEventListener('toggle', async () => {
            if (!box.open || box.dataset.got) return;
            box.dataset.got = '1';
            body.append(h('p', { text: 'Բեռնվում է…' }));
            try {
                const q = new URLSearchParams({ date: d.day, truck: truck.car_code, trip: String(tr.id), rev: String(d.rev) });
                const g = await api('GET', '/api/routes/warehouse/goods?' + q);
                body.textContent = '';
                if (!g.rows.length) { body.append(h('p', { text: 'Ապրանքի տողեր չկան։' })); return; }
                const ul = h('ul', null);
                g.rows.forEach(r => {
                    // упаковки «փաթեթ» и штуки (waybill: packs, loose); дробное или без упаковки — количество как есть
                    const unit = r.unit || 'հատ';
                    const parts = r.pack && r.packs !== null && r.packs !== undefined
                        ? [r.packs ? fmt(r.packs) + NB + 'փաթ.' : '', r.loose ? fmt(r.loose) + NB + unit : ''].filter(Boolean) : [];
                    const qty = parts.length ? parts.join(' + ') : fmt(r.qty) + NB + unit;
                    ul.append(h('li', null, h('span', { text: r.name || ('Ապրանք ' + r.code) }), h('span', { text: qty }),
                        h('small', { text: (r.code ? r.code + ' · ' : '') + fmt(r.kg) + NB + 'կգ' })));
                });
                body.append(ul, h('p', { text: 'Ընդամենը՝ ' + fmt(g.kg) + NB + 'կգ' }));
            } catch (e) {
                body.textContent = '';
                body.append(h('p', { text: e.message }));
                delete box.dataset.got;
                if (e.status === 409) markStale();
            }
        });
        return box;
    }

    function tripRow(d, truck, tr) {
        const meta = [h('b', { text: fmt(tr.kg) + NB + 'կգ' }), ' · ' + tr.stops + NB + 'խանութ',
            tr.preloaded ? ' · մեկնում՝ ' + tr.depart : ' · բեռնում՝ ' + tr.loading_start + ', մեկնում՝ ' + tr.depart];
        const title = h('h3', { class: 'wh-trip-t', text: tr.of > 1 ? 'Երթ ' + tr.no + ' / ' + tr.of : 'Երթ' });
        let act;
        if (tr.changing) {
            // №81: логист изменил рейс и ещё не отправил водителю — грузить нельзя, пока не уточнили
            act = h('span', { class: 'wh-changing' }, icon('fa-phone'), 'Լոգիստը փոխում է երթը՝ զանգահարեք');
        } else if (tr.loaded) {
            // №78, ответ 19: снять отметку можно в любое время — с вопросом (рейс снова может поменяться при պլանի վերակազմում)
            const u = h('button', { type: 'button', class: 'wh-undo', text: 'Հանել նշումը' });
            u.addEventListener('click', () => {
                if (window.confirm('Հանե՞լ «Բեռնված է» նշումը։ Լոգիստը կարող է նորից փոխել այս երթը։')) mark(tr, false, u);
            });
            act = h('div', { class: 'wh-trip-row' },
                h('span', { class: 'wh-done' }, icon('fa-check'), 'Բեռնված է ժ.' + NB + tr.loaded.at,
                    tr.loaded.by ? h('small', { text: '(' + tr.loaded.by + ')' }) : null), u);
        } else {
            const b = h('button', { type: 'button', class: 'wh-load' }, icon('fa-truck-ramp-box'), 'Բեռնված է');
            b.setAttribute('aria-label', 'Բեռնված է՝ ' + (truck.name || truck.car_code) + ', ' + title.textContent);
            b.addEventListener('click', () => mark(tr, true, b));
            act = b;
        }
        return h('div', { class: 'wh-trip' },
            h('div', { class: 'wh-trip-row' }, h('div', null, title, h('p', { class: 'wh-trip-meta' }, meta)), act),
            goods(d, truck, tr));
    }

    // ---------- Բեռնագիր машины (тот же документ, что печатает логист на «Развозе») ----------
    // Окно — сразу по нажатию (открытое после ответа сервера браузер счёл бы всплывающим). Не открылось (телефон,
    // блокировщик) — лист на этой же странице и печать её (printHere). Ответ сверяется с планом на экране после запроса:
    // rev и рейсы машины не те — «план изменился, обновите», а не лист по другому плану.
    async function printWaybill(truck, btn) {
        if (state.busy || state.stale || !state.data || btn.getAttribute('aria-busy') === 'true') return;
        const d = state.data;
        let w = null;
        try { w = window.open('', '_blank'); } catch (e) { w = null; }
        if (w) {
            w.document.write('<!doctype html><html lang="hy"><head><meta charset="utf-8"><title>Բեռնագիր</title></head>'
                + '<body style="font-family:Segoe UI,Sylfaen,Arial,sans-serif;padding:24px">Բեռնագիրը պատրաստվում է…</body></html>');
            w.document.close();
        }
        btn.setAttribute('aria-busy', 'true');
        let wb, t;
        try {
            const q = new URLSearchParams({ date: d.day, truck: truck.car_code, rev: String(d.rev) });
            wb = await api('GET', '/api/routes/warehouse/waybill?' + q);
            const cur = state.data;
            t = cur && cur.day === d.day ? cur.trucks.find(x => x.car_code === truck.car_code) : null;
            const same = !!t && cur.rev === wb.rev && Array.isArray(wb.trips) && wb.trips.length === t.trips.length
                && wb.trips.every((x, i) => x.id === t.trips[i].id);
            if (!same) throw Object.assign(new Error('Պլանը փոխվել է — թարմացրեք էջը'), { status: 409 });
        } catch (e) {
            if (w) { try { w.close(); } catch (x) { /* уже закрыто */ } }
            if (e.status === 409) markStale(); else showAlert(e.message, false);
            return;
        } finally {
            btn.removeAttribute('aria-busy');
        }
        const html = window.RtWaybill.html({ car_code: t.car_code, name: t.name }, { day: wb.day, weekday: wb.weekday }, wb);
        if (w && w.closed) return;            // окно закрыли, пока шёл запрос
        if (w) {
            w.document.open();
            w.document.write(html);
            w.document.close();
            w.focus();
            setTimeout(() => { try { w.print(); } catch (e) { /* окно закрыли раньше */ } }, 300);
            return;
        }
        printHere(html);
    }

    // Печать без окна: лист — в теневом DOM узла #whPrint прямо в body (стили листа и страницы не смешиваются: body листа
    // — :host узла), при печати виден только он (routes_warehouse.css, body.wh-printing). Узел остаётся до следующего листа:
    // на телефоне print() не ждёт диалога, убрать лист сразу — напечатался бы пустой.
    function printHere(html) {
        let host = $('whPrint');
        if (!host) {
            host = h('div', { id: 'whPrint' });
            host.attachShadow({ mode: 'open' });
            document.body.append(host);
        }
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const style = document.createElement('style');
        style.textContent = Array.from(doc.querySelectorAll('style'), s => s.textContent).join('')
            .replace(/(^|[{}])body\{/g, '$1:host{');
        host.shadowRoot.replaceChildren(style, ...Array.from(doc.body.childNodes));
        document.body.classList.add('wh-printing');
        announce('Բեռնագիրը պատրաստ է տպելու');
        setTimeout(() => window.print(), 50);
    }

    function render() {
        renderDays();
        const d = state.data, list = $('whList');
        list.textContent = '';
        list.setAttribute('aria-busy', 'false');
        $('whSummary').textContent = '';
        if (!d) return;
        if (!d.planned) { list.append(h('p', { class: 'wh-empty', text: 'Այս օրվա պլանը դեռ կազմված չէ։' })); return; }
        if (!d.approved) {
            list.append(h('p', { class: 'wh-empty', text: 'Պլանը դեռ հաստատված չէ — սպասեք լոգիստի «Հաստատել»-ին։' }));
            return;
        }
        const trips = d.trucks.flatMap(t => t.trips);
        const done = trips.filter(t => t.loaded).length;
        $('whSummary').textContent = d.trucks.length + NB + 'մեքենա · ' + trips.length + NB + 'երթ · բեռնված՝ ' + done + ' / ' + trips.length;
        d.trucks.forEach(t => {
            const pr = h('button', { type: 'button', class: 'wh-print', 'aria-label': 'Տպել բեռնագիրը՝ ' + (t.name || t.car_code) },
                icon('fa-print'), 'Տպել բեռնագիրը');
            pr.addEventListener('click', () => printWaybill(t, pr));
            list.append(h('section', { class: 'wh-truck', 'aria-label': t.name || t.car_code },
                h('div', { class: 'wh-truck-head' }, h('h2', { class: 'wh-truck-name', text: t.name || t.car_code }),
                    t.name ? h('span', { class: 'wh-truck-code', text: t.car_code }) : null),
                h('div', { class: 'wh-truck-meta' },
                    h('p', { class: 'wh-driver' }, icon('fa-id-card'), t.driver ? t.driver : 'վարորդը նշված չէ'), pr),
                t.trips.map(tr => tripRow(d, t, tr))));
        });
    }

    function markStale() {
        state.stale = true;
        showAlert('Պլանը փոխվել է — թարմացրեք էջը', true);
    }

    async function load(day) {
        state.busy = true;
        $('whList').setAttribute('aria-busy', 'true');
        try {
            const data = await api('GET', '/api/routes/warehouse' + (day ? '?date=' + encodeURIComponent(day) : ''));
            state.data = data;
            state.day = data.day;
            state.stale = false;
            showAlert('');
            render();
        } catch (e) {
            showAlert(e.message, true);
            $('whList').setAttribute('aria-busy', 'false');
        } finally {
            state.busy = false;
        }
    }

    async function mark(tr, loaded, btn) {
        if (state.busy || !state.data) return;
        state.busy = true;
        btn.disabled = true;
        try {
            const data = await api('POST', '/api/routes/warehouse/loaded',
                { date: state.data.day, rev: state.data.rev, trip: tr.id, loaded });
            state.data = data;
            showAlert('');
            render();
            announce(loaded ? 'Նշված է՝ բեռնված է' : 'Նշումը հանված է');
        } catch (e) {
            btn.disabled = false;
            if (e.status === 409) markStale(); else showAlert(e.message, false);
        } finally {
            state.busy = false;
        }
    }

    $('whReload').addEventListener('click', () => load(state.day));
    // план меняет логист: раз в минуту, пока страница видна, — свежий план (без прыжка, если что-то открыто)
    setInterval(() => {
        if (document.visibilityState !== 'visible' || state.busy || state.stale) return;
        if (document.querySelector('.wh-goods[open]')) return;
        load(state.day);
    }, REFRESH_MS);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && !state.busy && !state.stale && state.day) load(state.day);
    });
    load(null);
})();

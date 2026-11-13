/* Предпросмотр и результат native-цепочки; данные ERP выводятся только через textContent. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const prefix = '/api/routes/dispatch/erp-chain/';
    const labels = { complete: 'Ավարտված է', partial: 'Մասնակի ավարտ', unknown: 'Արդյունքը պետք է ստուգել',
        running: 'Գրանցումն ընթացքի մեջ է', refused: 'Գրանցումը կանգնեցված է', ready: 'Նախադիտում' };
    const kinds = { assignment: 'Առաքիչ և մեքենա', providing: 'Տրամադրում', sale: 'Վաճառք' };
    const mayStillRun = 'Պատասխանը չի ստացվել, գրանցումը կարող է դեռ ընթանալ։ Սեղմեք «Ստուգել արդյունքը»։ Նորից գրանցում չի կատարվի։';
    // Обрыв связи, таймаут браузера или 502/504 шлюза: запись могла дойти до ERP и ещё идти.
    const responseLost = error => error.status === 0 || error.status === 502 || error.status === 504;
    let day, io, csrf, trips = [], token = null, busy = false, executable = false, reviewId = null, courierNames = new Map(), couriersUnread = false,
        orderRows = [], overrides = new Map(), ticked = new Set(), documentedTrips = [], claimedRows = [], limit = null, estimate = 0;
    const element = (tag, text, cls) => {
        const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
        if (cls) el.className = cls; return el;
    };
    const amount = n => Number(n).toLocaleString('hy-AM', { maximumFractionDigits: 2 });
    const durationText = s => s < 120 ? `${s} վայրկյան` : `${Math.round(s / 60)} րոպե`;
    // Ход записи ERP-цепочки для окна у кнопок (13.11: 31 заказ писался 73 с без признаков жизни, логист решил, что окно зависло):
    // полоса и шаги с числами из ERP (progress/<id> раз в 2 с; сайт читает ERP сам) и время. Пока открыт пароль Superadmin
    // (routes_superadmin.js, #rtSuperadminDialog), записи ещё нет: отсчёт стоит, ERP не читается.
    const STAGES = ['assignment', 'providing', 'sale'];
    function runClock(id) {
        const box = element('div', undefined, 'dp-chain-progress is-waiting');
        const head = element('div', undefined, 'dp-chain-progress-head'), pct = element('span', '', 'dp-chain-progress-pct');
        head.append(element('strong', 'Գրանցում ERP-ում'), pct);
        const track = element('div', undefined, 'dp-chain-progress-track'), fill = element('div', undefined, 'dp-chain-progress-fill');
        track.append(fill);
        track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', labels.running);
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100');
        const list = element('ol', undefined, 'dp-chain-stages'), counts = {};
        STAGES.forEach(kind => {
            const item = element('li'); item.dataset.kind = kind; counts[kind] = element('b', '—');
            item.append(element('span', undefined, 'dp-chain-stage-icon'), element('span', kinds[kind]), counts[kind]);
            list.append(item);
        });
        const time = element('p', '', 'dp-chain-progress-time');
        box.append(head, track, list, time, element('p', 'Մի փակեք պատուհանը և մի թարմացրեք էջը։', 'dp-chain-progress-note'));
        $('dpChainExecute').parentElement.prepend(box);
        // Остаток — плавный обратный отсчёт: скорость — только по шагам этой записи, с первого изменения после первого чтения
        // (base); пока их меньше трёх — 1,2 с на шаг (замер 13.11). Показ каждую секунду тянется к расчёту, а не прыгает.
        // Числа шагов не убывают (best): чтение без блокировок может на миг увидеть документ и потерять его.
        let started = Date.now(), done = 0, total = 0, base = null, firstAt = null, firstDone = 0, shown = null, lastTick = 0;
        let stopped = false, poller = null;
        const best = {};
        const waiting = () => { const p = document.getElementById('rtSuperadminDialog'); return !!(p && p.open); };
        const elapsed = () => Math.round((Date.now() - started) / 1000);
        const tick = () => {
            if (waiting()) { started = Date.now(); base = null; firstAt = null; shown = null; }
            const s = elapsed(), now = Date.now(), dt = lastTick ? (now - lastTick) / 1000 : 0;
            lastTick = now;
            if (firstAt !== null && total > done) {
                const perStep = done - firstDone >= 3 && s > firstAt ? (s - firstAt) / (done - firstDone) : 1.2;
                const target = (total - done) * perStep;
                shown = shown === null ? target : Math.max(1, shown - dt + (target - (shown - dt)) * Math.min(1, 0.3 * dt));
            } else shown = null;
            const left = shown === null ? null : shown > 30 ? Math.round(shown / 5) * 5 : Math.max(1, Math.round(shown));
            time.textContent = `Անցել է ${s} վ` + (left !== null ? ` · մնացել է մոտ ${durationText(left)}`
                : total && done >= total ? ' · վերջնական ստուգում' : s <= estimate ? ` · ընդհանուր մոտ ${durationText(estimate)}` : '');
        };
        const show = p => {
            const steps = STAGES.map(kind => {
                const x = Array.isArray(p[kind]) ? p[kind] : [0, 0];
                best[kind] = Math.max(best[kind] || 0, Number(x[0]) || 0);
                return [best[kind], Number(x[1]) || 0];
            });
            done = steps.reduce((t, x) => t + x[0], 0); total = steps.reduce((t, x) => t + x[1], 0);
            if (base === null) base = done;
            else if (firstAt === null && done > base) { firstAt = elapsed(); firstDone = done; }
            const percent = total ? Math.min(100, Math.round(done / total * 100)) : 0;
            box.classList.remove('is-waiting');
            fill.style.width = percent + '%'; pct.textContent = percent + '%'; track.setAttribute('aria-valuenow', String(percent));
            list.querySelectorAll('li').forEach((item, i) => {
                const [d, n] = steps[i];
                counts[item.dataset.kind].textContent = `${d} / ${n}`;
                item.className = n && d >= n ? 'is-done' : d > 0 ? 'is-active' : '';
            });
            tick();
        };
        const poll = async () => {
            if (stopped) return;
            if (!waiting()) {
                try {
                    const data = await io.read('GET', prefix + 'progress/' + encodeURIComponent(id), undefined, 8000);
                    if (!stopped && data && data.progress) show(data.progress);
                } catch (error) { /* ход — только подсказка: сбой чтения не мешает записи и не показывается */ }
            }
            if (!stopped) poller = setTimeout(poll, 2000);
        };
        tick();
        const timer = setInterval(tick, 1000);
        poller = setTimeout(poll, 1000);
        return () => { stopped = true; clearInterval(timer); clearTimeout(poller); box.remove(); };
    }
    function controls() {
        ['dpChainTrip', 'dpChainClose'].forEach(id => { $(id).disabled = busy; });
        $('dpChainPreview').disabled = busy || couriersUnread || !trips.length;
        $('dpChainExecute').disabled = busy || !executable;
        $('dpChainCheck').disabled = busy;
        $('dpChainReconcilePreview').disabled = busy;
        $('dpChainReconcileReason').disabled = busy;
        $('dpChainReconcile').disabled = busy || !reviewId || $('dpChainReconcileReason').value.trim().length < 10;
        $('dpChainCouriers').querySelectorAll('select').forEach(select => { select.disabled = busy; });
        $('dpChainOrders').querySelectorAll('input').forEach(input => { input.disabled = busy; });
        $('dpChainGroupWarehouse').disabled = busy;
        groupControls();
        $('dpChainDlg').setAttribute('aria-busy', String(busy));
    }
    async function run(fn) {
        if (busy) return;
        busy = true; $('dpChainError').textContent = ''; controls();
        try { await fn(); } catch (error) { $('dpChainError').textContent = error.message || 'Սերվերի հետ կապ չկա։'; }
        finally { busy = false; controls(); }
    }
    function resetPreview() {
        token = null; executable = false; $('dpChainBody').replaceChildren();
        reviewId = null;
        ['dpChainReconcileFields', 'dpChainReconcile', 'dpChainReconcilePreview'].forEach(id => { $(id).hidden = true; });
        $('dpChainSummary').textContent = '';
        $('dpChainExecute').hidden = false; $('dpChainCheck').hidden = true;
        $('dpChainHint').textContent = 'Նախ ստուգեք պատվերները։'; controls();
    }
    function courierRows(trucks) {
        trucks.forEach(truck => {
            truck.candidates.forEach(card => courierNames.set(card.code, card.name));
            const select = element('select', undefined, 'rt-select');
            select.dataset.car = truck.car;
            select.dataset.person = truck.person === null ? '' : String(truck.person);
            select.setAttribute('aria-label', 'Վարորդի ERP կոդը');
            if (!truck.code) select.append(new Option('Ընտրեք կոդը', ''));
            truck.candidates.forEach(card => select.append(new Option(card.code + ' · ' + card.name, card.code)));
            select.value = truck.code || '';
            select.addEventListener('change', resetPreview);
            const row = element('div', undefined, 'dp-chain-meta');
            row.append(truck.car + ' ', select,
                element('div', truck.person === null ? 'PIN-ը դեռ չի մուտքագրվել' : `PIN՝ ${truck.person_name || ''}`));
            $('dpChainCouriers').append(row);
        });
    }
    const courierChoices = () => Object.fromEntries([...$('dpChainCouriers').querySelectorAll('select')]
        .map(select => [select.dataset.car, { code: select.value, person: select.dataset.person === '' ? null : Number(select.dataset.person) }]));
    const courierLabel = code => courierNames.has(code) ? code + ' · ' + courierNames.get(code) : code;
    const tripVisible = id => { const trip = $('dpChainTrip').value; return trip === '*' || trip === '' || trip === id; };
    const visibleOrders = () => orderRows.filter(order => tripVisible(order.trip));
    // Кто сделал տրամադրում: номер пользователя ՀԾ (имён в базе продаж нет); время — без даты, если это день окна.
    function madeText(p) {
        const who = p.user === null || p.user === undefined ? 'ՀԾ' : 'ՀԾ օգտատեր ' + p.user;
        if (!p.made) return who;
        const date = p.made.slice(0, 10), time = p.made.slice(11);
        return who + ', ' + (date === day ? time : date.slice(8, 10) + '.' + date.slice(5, 7) + ' ' + time);
    }
    function documentedLine(t) {
        const shown = t.providings.slice(0, 3).map(p => `${p.number} (${madeText(p)})`).join(', ');
        const more = t.providings.length > 3 ? ` և ևս ${t.providings.length - 3}` : '';
        return `${t.car} · Երթ ${t.trip}՝ ${t.count} պատվեր արդեն ձևակերպված է ՀԾ-ում և ցուցակում չկա`
            + (t.providings.length ? `․ տրամադրում ${shown}${more}` : '') + '։';
    }
    // complete без документов — их удалили в ՀԾ; refused — native ничего не записал, но заказ остаётся занятым навсегда.
    const claimedText = row => row.deleted
        ? 'կայքի ստեղծած տրամադրումն ու վաճառքը ջնջվել են ՀԾ-ում։ Կայքը դրանք նորից չի ստեղծի․ ձևակերպեք պատվերը ՀԾ-ում ձեռքով։'
        : row.status === 'refused'
            ? 'կայքի նախորդ գրանցումը կանգնեցվել է առանց փաստաթղթերի, և կայքը այս պատվերը նորից չի վերցնի։ Ձևակերպեք պատվերը ՀԾ-ում ձեռքով։'
            : `ընդգրկված է կայքի նախորդ գրանցման մեջ (${labels[row.status] || row.status})։ Ստուգեք դրա արդյունքը։`;
    const tripDone = id => documentedTrips.some(t => t.trip === id) && !orderRows.some(o => o.trip === id) && !claimedRows.some(o => o.trip === id);
    function groupControls() {
        $('dpChainGroupApply').disabled = busy || !$('dpChainGroupWarehouse').value || !visibleOrders().some(order => ticked.has(order.isn));
    }
    function renderOrders() {
        const visible = visibleOrders();
        const all = element('input'); all.type = 'checkbox'; all.setAttribute('aria-label', 'Ընտրել բոլորը');
        all.checked = visible.length > 0 && visible.every(order => ticked.has(order.isn));
        all.addEventListener('change', () => {
            visible.forEach(order => { if (all.checked) ticked.add(order.isn); else ticked.delete(order.isn); });
            renderOrders();
        });
        const head = element('tr'); const allCell = element('th'); allCell.append(all); head.append(allCell);
        ['Պատվեր', 'Խանութ', 'Երթ', 'Բաժին', 'Պահեստ'].forEach(title => head.append(element('th', title)));
        const thead = element('thead'); thead.append(head);
        const body = element('tbody');
        visible.forEach(order => {
            const changed = overrides.has(order.isn), code = changed ? overrides.get(order.isn) : order.warehouse;
            const unsettled = order.reserved_elsewhere && !changed;
            const parts = [code || 'Պահեստ չկա'];
            if (changed) parts.push('փոխված');
            if (order.reserved_elsewhere) parts.push('պահուստը՝ ' + order.reserved.join(', '));
            if (unsettled && code) parts.push('ընտրեք պահեստը');
            const tick = element('input'); tick.type = 'checkbox'; tick.checked = ticked.has(order.isn); tick.setAttribute('aria-label', 'Ընտրել պատվերը');
            tick.addEventListener('change', () => {
                if (tick.checked) ticked.add(order.isn); else ticked.delete(order.isn);
                all.checked = visible.every(row => ticked.has(row.isn)); groupControls();
            });
            const tickCell = element('td'); tickCell.append(tick);
            const row = element('tr', undefined, changed ? 'dp-chain-changed' : undefined);
            const divisionCell = element('td', order.division_name || order.division || '—');
            if (order.division_name) divisionCell.title = order.division;
            row.append(tickCell, element('td', order.number), element('td', order.store), element('td', order.trip), divisionCell,
                element('td', parts.join(' · '), code && !unsettled ? undefined : 'dp-chain-warning'));
            body.append(row);
        });
        const list = element('table'); list.append(thead, body);
        const open = visible.filter(order => order.reserved_elsewhere && !overrides.has(order.isn)).length;
        const held = claimedRows.filter(row => tripVisible(row.trip)).map(row => element('p', `${row.number} · ${row.store}՝ ${claimedText(row)}`, 'dp-chain-warning'));
        $('dpChainOrders').replaceChildren(...held,
            ...(open ? [element('p', `${open} պատվերի պահուստն այլ պահեստում է։ Նշեք դրանք և ընտրեք պահեստը, նույնիսկ եթե այն չի փոխվում։`, 'dp-chain-warning')] : []),
            ...(visible.length ? [list] : []));
        const lines = documentedTrips.filter(t => tripVisible(t.trip)).map(documentedLine);
        $('dpChainLeftOut').replaceChildren(...lines.flatMap((text, i) => i ? [element('br'), text] : [text]));
        $('dpChainLeftOut').hidden = !lines.length;
        groupControls();
    }
    function table(headers, rows) {
        const wrap = element('div', undefined, 'dp-chain-table'); const t = element('table');
        const head = element('tr'); headers.forEach(x => head.append(element('th', x)));
        const thead = element('thead'); thead.append(head); t.append(thead);
        const body = element('tbody'); rows.forEach(row => { const tr = element('tr'); row.forEach(x => tr.append(element('td', x))); body.append(tr); });
        t.append(body); wrap.append(t); return wrap;
    }
    function previewResult(result) {
        token = result.id;
        // Мост откажет too_many только после подтверждения: при заказах сверх лимита кнопку не даём, а говорим, сколько их и сколько можно.
        const over = limit !== null && result.order_count > limit;
        const overText = `Նախադիտման մեջ ${result.order_count} պատվեր կա, իսկ մեկ գործարկումում թույլատրված է առավելագույնը ${limit}։ Ընտրեք ավելի քիչ պատվերով երթ, ձևակերպեք պատվերները ձեռքով ՀԾ-ում կամ դիմեք սեփականատիրոջը։`;
        executable = result.order_count > 0 && !over;
        // Замер 13.11: 65 записей ERP (водитель в заказ, տրամադրում, վաճառք) за 73 с, плюс ~10 с на запуск native.
        estimate = Math.ceil((10 + 1.2 * (2 * result.order_count + result.loads.length)) / 5) * 5;
        $('dpChainRecent').hidden = true;
        $('dpChainSummary').textContent = `${result.order_count} պատվեր · ${result.loads.length} տրամադրում · ${result.order_count} վաճառք`;
        const box = $('dpChainBody'); box.replaceChildren();
        if (over) box.append(element('p', overText, 'dp-chain-warning'));
        result.loads.forEach(load => {
            const section = element('section', undefined, 'dp-chain-load');
            section.append(element('h3', `${load.car} · Երթ ${String(load.trip).split('/')[0]} · Պահեստ ${load.warehouse}`),
                element('p', `Առաքիչ՝ ${courierLabel(load.courier)}`, 'dp-chain-meta'));
            section.append(table(['Պատվեր', 'Գումար'], load.orders.map(o => [o.number, amount(o.sum) + ' ֏'])));
            const operations = { RLZ: 'Վաճառք', GFT: 'Նվեր', DEP: 'Ի պահ', EXC: 'Փոխանակում' };
            section.append(table(['Ապրանք', 'Գործողություն', 'Քանակ'], load.manifest.map(r => [
                load.product_names && load.product_names[r.product] ? load.product_names[r.product] + ' · ' + r.product : r.product,
                operations[r.operation] || r.operation, amount(r.quantity)])));
            if (load.warnings.length) section.append(element('p', 'Առաքիչի որոշ ապրանքների սկզբնական մնացորդը բացասական է։ Այս գործողությունը չի ուղղում նախկին մնացորդները։', 'dp-chain-warning'));
            box.append(section);
        });
        if (result.skipped.length) {
            box.append(element('h3', `Բաց թողնված՝ ${result.skipped.length}`));
            const documented = result.skipped.filter(row => row.reason !== 'claimed');
            if (documented.length) {
                const details = element('details');
                details.append(element('summary', `${documented.length} պատվեր արդեն ձևակերպված է ՀԾ-ում`),
                    element('p', documented.map(row => row.number || row.isn).join(', '), 'dp-chain-meta'));
                box.append(details);
            }
            const held = new Map(claimedRows.map(row => [row.isn.toUpperCase(), row]));
            const claimed = result.skipped.filter(row => row.reason === 'claimed');
            if (claimed.length) {
                const list = element('ul');
                claimed.forEach(row => {
                    const known = held.get(String(row.isn).toUpperCase());
                    list.append(element('li', `${row.number || row.isn}: ${known ? claimedText(known) : 'արդեն ընդգրկված է այլ գրանցման մեջ'}`));
                });
                box.append(list);
            }
        }
        $('dpChainHint').textContent = over ? overText : executable
            ? 'Հաստատելուց հետո կգրանցվեն առաքիչն ու մեքենան, ապա տրամադրումը և վաճառքը։ Նախորդ հաջողված քայլերը մնում են ERP-ում, եթե հաջորդ քայլը կանգնի։'
                + ` Գրանցումը կտևի մոտ ${durationText(estimate)}։`
            : 'Ստեղծելու համար հասանելի պատվեր չկա։ Նախկին գրանցման արդյունքը կարող եք ստուգել վերևից։';
        $('dpChainExecute').hidden = false; $('dpChainCheck').hidden = true;
    }
    function releaseSection(result) {
        const section = element('section', undefined, 'dp-chain-load');
        section.append(element('h3', 'Ազատել պլանը ձեռքով'),
            element('p', 'Օգտագործեք միայն ՀԾ-ում փաստաթղթերը ձեռքով ստուգելուց հետո։ Կհանվի միայն պլանի կողպեքը․ պատվերները մնում են զբաղված։', 'dp-chain-meta'));
        const reason = element('textarea', undefined, 'rt-input');
        reason.rows = 2; reason.maxLength = 500; reason.placeholder = 'Նշեք՝ ինչ եք ստուգել ՀԾ-ում (10–500 նիշ)։';
        reason.setAttribute('aria-label', 'Պլանն ազատելու պատճառը');
        const button = element('button', 'Ազատել պլանը', 'rt-btn rt-btn-ghost'); button.type = 'button'; button.disabled = true;
        reason.addEventListener('input', () => { button.disabled = reason.value.trim().length < 10; });
        button.addEventListener('click', () => run(async () => {
            const data = await io.write('POST', prefix + 'release-plan', { csrf, id: result.id, reason: reason.value }, 160000);
            statusResult(data.result);
        }));
        section.append(reason, button);
        return section;
    }
    // Итог записи — цветной блок сверху: успех / внимание / ничего не записано; таблица шагов — под «Մանրամասն», шаги с проблемой первыми.
    // Успех — только status complete; native refused и отказ сайта до записи — «ничего не записано».
    const stepOk = step => step.status === 'verified' && !!step.observed_registered;
    // confirmed_absent — проверено, что не создан (сверка создала замену): не проблема, вниз не уходит и не считается
    const stepSettled = step => stepOk(step) || step.status === 'confirmed_absent';
    const orderNumbers = result => new Map((Array.isArray(result.loads) ? result.loads : [])
        .flatMap(load => (Array.isArray(load.orders) ? load.orders : []).map(o => [String(o.isn || '').toLowerCase(), o.number])));
    function outcome(result, steps, rows) {
        const tone = result.status === 'complete' ? 'ok' : result.status === 'running' ? 'run'
            : result.status === 'refused' || result.no_write_dispatched === true ? 'idle' : 'warn';
        const box = element('div', undefined, 'dp-chain-outcome is-' + tone);
        // Итоги — по самой записи (её заказы и загрузки): шаг появляется только перед своей записью, и у заказов, до
        // которых запись не дошла, шагов нет совсем. Полей нет — по шагам.
        const loads = Array.isArray(result.loads) ? result.loads : [];
        const orders = Array.isArray(result.claimed_orders) && result.claimed_orders.length ? result.claimed_orders.length
            : new Set(steps.filter(s => s.order_isn).map(s => s.order_isn)).size;
        const providings = loads.length || steps.filter(s => s.kind === 'providing').length;
        const ok = kind => steps.filter(s => s.kind === kind && stepOk(s)).length;
        const icon = element('span', undefined, 'dp-chain-outcome-icon');
        icon.setAttribute('aria-hidden', 'true');
        const text = element('div');
        if (tone === 'ok') {
            const numbers = steps.filter(s => s.kind === 'providing' && stepOk(s)).map(s => s.observed_number || s.number).filter(Boolean);
            text.append(element('strong', 'Բոլոր փաստաթղթերը գրանցված են'),
                element('p', `${orders} պատվեր · ${ok('providing')} տրամադրում · ${ok('sale')} վաճառք`),
                ...(numbers.length ? [element('p', 'Տրամադրում՝ ' + numbers.join(', '))] : []));
        } else {
            text.append(element('strong', tone === 'idle' ? 'ERP-ում ոչինչ չի գրանցվել' : labels[result.status] || 'Արդյունքը պետք է ստուգել'));
            if (tone === 'warn' && orders) text.append(element('p', `Հաստատված է՝ ${kinds.assignment} ${ok('assignment')} / ${orders} · `
                + `${kinds.providing} ${ok('providing')} / ${providings} · ${kinds.sale} ${ok('sale')} / ${orders}`));
        }
        // drift_detected native ставит и при недописанном шаге; правка в ՀԾ — только когда он понизил завершённую запись
        if (result.drift_detected === true && result.journal_status === 'complete')
            text.append(element('p', 'Գրանցումից հետո փաստաթղթերը փոխվել են ՀԾ-ում։'));
        box.append(icon, text);
        if (!rows.length) return [box];
        const details = element('details', undefined, 'dp-chain-details');
        details.open = tone === 'warn';
        details.append(element('summary', `Մանրամասն՝ ${rows.length} քայլ`), table(['Քայլ', 'Փաստաթուղթ', 'Արդյունք'], rows));
        return [box, details];
    }
    function statusResult(result) {
        token = result.id; executable = false;
        reviewId = null; $('dpChainReconcileFields').hidden = true; $('dpChainReconcile').hidden = true;
        // refused: сверять нечего — native ставит refused только при нуле записей, шага տրամադրում нет, сверка всегда ответит providing_missing
        $('dpChainReconcilePreview').hidden = !['unknown', 'partial'].includes(result.status) || result.no_write_dispatched === true;
        $('dpChainSummary').textContent = labels[result.status] || 'Արդյունքը պետք է ստուգել';
        const steps = [...(result.steps || [])].sort((a, b) => stepSettled(a) - stepSettled(b));   // sort устойчив: порядок внутри сохраняется
        const orderNo = orderNumbers(result);
        const saleOf = step => step.kind === 'sale' ? orderNo.get(String(step.order_isn || '').toLowerCase()) : undefined;
        const rows = steps.map(step => [(kinds[step.kind] || step.kind) + (step.warehouse ? ` · Պահեստ ${step.warehouse}` : '')
            + (saleOf(step) ? ` · պատվեր ${saleOf(step)}` : ''),
            step.observed_number || step.number || '—',
            step.status === 'missing' ? 'Չի ստեղծվել' : step.status === 'confirmed_absent' ? 'Ստուգված է․ չի գրանցվել' : step.status === 'verified' && step.observed_registered ? 'Հաստատված է' : step.observed_registered ? 'Գրանցված է․ պահանջվում է ստուգում' : 'Չհաստատված']);
        $('dpChainBody').replaceChildren(...outcome(result, steps, rows));
        $('dpChainBody').firstElementChild.scrollIntoView({ block: 'nearest' });
        if (result.plan_released) $('dpChainBody').append(element('p', 'Պլանը ձեռքով ազատված է։ Պատվերները մնում են զբաղված։', 'dp-chain-meta'));
        else if (result.stale_lease === true || (['partial', 'unknown'].includes(result.status) && result.no_write_dispatched !== true)) $('dpChainBody').append(releaseSection(result));
        $('dpChainExecute').hidden = true; $('dpChainCheck').hidden = false;
        $('dpChainHint').textContent = result.status === 'complete'
            ? 'Փաստաթղթերը գրանցված են և կապված են սկզբնական պատվերներին։ Արդյունքի կրկնակի ստուգումը նոր փաստաթուղթ չի ստեղծում։'
            : result.status === 'running'
                ? 'ERP-ի գործողությունը դեռ ընթանում է։ Մի փոքր անց կրկին սեղմեք «Ստուգել արդյունքը»։'
            : result.no_write_dispatched === true
                ? 'Այս փորձով գրանցում չի կատարվել։ Շարունակելու համար բացեք նոր նախադիտում։'
            : result.status === 'refused'
                ? 'ERP-ում ոչինչ չի գրանցվել, բայց կայքը այս պատվերները նորից չի վերցնի։ Ձևակերպեք դրանք ՀԾ-ում ձեռքով։'
            : (result.steps || []).some(step => step.status === 'missing')
                ? 'Մի մասը չի գրանցվել։ Ավարտեք գրանցումը ՀԾ-ում ձեռքով, ապա ազատեք պլանը։'
                : 'Շղթան կանգնեցված է։ Ստուգեք արդեն գրանցված փաստաթղթերը։ «Ստուգել արդյունքը» գործողությունը միայն կարդում է ERP-ից։';
    }
    async function check(id) {
        executable = false;
        $('dpChainSummary').textContent = 'Ստուգում եմ գրանցված փաստաթղթերը…';
        const data = await io.read('GET', prefix + 'status/' + encodeURIComponent(id), undefined, 160000);
        if (data.result.status === 'preview' && data.result.stale_lease !== true) previewResult(data.result);
        else statusResult(data.result);
    }
    function showTarget(data) {
        limit = data.writes_enabled && Number.isInteger(data.max_orders) ? data.max_orders : null;
        $('dpChainTarget').textContent = (data.environment === 'isolated-test' ? 'Փորձնական բազա՝ ' : 'Գործող ERP (գրանցումները իրական են)՝ ') + data.database
            + (data.writes_enabled ? ` · գրանցումը միացված է, մեկ գործարկումում առավելագույնը ${data.max_orders} պատվեր` : ' · գրանցումն անջատված է');
    }
    async function open(selectedDay, callbacks) {
        if (busy) return;
        day = selectedDay; io = callbacks; csrf = null; trips = []; resetPreview();
        $('dpChainTrip').replaceChildren(); $('dpChainRecent').replaceChildren(); $('dpChainRecent').hidden = true;
        $('dpChainCouriers').replaceChildren(); courierNames = new Map(); couriersUnread = false;
        orderRows = []; overrides = new Map(); ticked = new Set(); documentedTrips = []; claimedRows = [];
        $('dpChainOrders').replaceChildren(); $('dpChainOrdersBox').hidden = true;
        $('dpChainGroupWarehouse').replaceChildren(new Option('Ընտրեք պահեստը', ''), new Option('Ըստ բաժնի', '=')); $('dpChainLeftOut').hidden = true;
        $('dpChainSummary').textContent = 'Կարդում եմ ERP-ից…';
        $('dpChainDlg').showModal(); $('dpChainTitle').focus();
        await run(async () => {
            const data = await io.read('GET', prefix + 'options?date=' + encodeURIComponent(day), undefined, 160000);
            csrf = data.csrf; trips = data.trips;
            showTarget(data);
            courierRows(data.trucks || []);
            couriersUnread = data.trips.length > 0 && (!(data.trucks || []).length || data.orders === null);
            orderRows = data.orders || []; documentedTrips = data.documented || []; claimedRows = data.orders_claimed || [];
            $('dpChainTrip').append(new Option('Բոլոր երթերը', '*'));
            trips.forEach(t => $('dpChainTrip').append(new Option(t.car + ' · Երթ ' + t.id + (tripDone(t.id) ? ' · ձևակերպված է' : ''), t.id)));
            data.warehouses.forEach(w => $('dpChainGroupWarehouse').append(new Option(w.code + ' · ' + w.name, w.code)));
            $('dpChainOrdersBox').hidden = !orderRows.length && !documentedTrips.length && !claimedRows.length;
            renderOrders();
            $('dpChainSummary').textContent = couriersUnread
                ? 'Չհաջողվեց կարդալ վարորդների ERP կոդերը։ Փակեք և կրկին բացեք պատուհանը։'
                : 'Ընտրեք երթերը և սեղմեք «Ստուգել»։';
            const recent = $('dpChainRecent');
            if (data.recent.length) {
                recent.hidden = false;
                const details = element('details');
                details.append(element('summary', `Վերջին գրանցումները (${data.recent.length})`));
                data.recent.forEach((r, i) => {
                    const button = element('button', `${r.label || i + 1} · ${labels[r.status] || r.status}`, 'rt-linkbtn'); button.type = 'button';
                    button.addEventListener('click', () => run(() => check(r.id))); details.append(button);
                });
                recent.append(details);
            }
        });
    }
    $('dpChainTrip').addEventListener('change', () => { resetPreview(); renderOrders(); });
    $('dpChainGroupWarehouse').addEventListener('change', groupControls);
    $('dpChainGroupApply').addEventListener('click', () => {
        const code = $('dpChainGroupWarehouse').value;
        // «Ըստ բաժնի» снимает выбор; склад направления у заказа с резервом в другом складе — тоже явный выбор.
        visibleOrders().filter(order => ticked.has(order.isn)).forEach(order => {
            if (code === '=' || (code === order.warehouse && !order.reserved_elsewhere)) overrides.delete(order.isn);
            else overrides.set(order.isn, code);
        });
        ticked.clear(); $('dpChainGroupWarehouse').value = '';
        resetPreview(); renderOrders();
    });
    $('dpChainPreview').addEventListener('click', () => run(async () => {
        resetPreview(); $('dpChainSummary').textContent = 'Ստուգում եմ պատվերներն ու հասանելի քանակները…';
        const chosen = $('dpChainTrip').value;
        const picked = chosen === '*' ? trips.map(t => t.id) : [chosen];
        // Только выбор заказов отмеченных рейсов: склад заказа чужого рейса сервер отклоняет как bad_request.
        const warehouses = Object.fromEntries(orderRows.filter(order => overrides.has(order.isn) && picked.includes(order.trip))
            .map(order => [order.isn, overrides.get(order.isn)]));
        try {
            const data = await io.read('POST', prefix + 'preview', { csrf, date: day,
                trips: picked, couriers: courierChoices(), warehouses }, 160000);
            showTarget(data.target);
            previewResult(data.result);
        } catch (error) { $('dpChainSummary').textContent = ''; throw error; }
    }));
    $('dpChainExecute').addEventListener('click', () => run(async () => {
        if (!executable || !token) return;
        executable = false; $('dpChainExecute').disabled = true;
        $('dpChainSummary').textContent = 'Գրանցում եմ տրամադրումն ու վաճառքը…';
        // Disable replay immediately, including lost HTTP responses and cancelled password prompts.
        $('dpChainExecute').hidden = true; $('dpChainCheck').hidden = false;
        const stopClock = runClock(token);
        try {
            const data = await io.write('POST', prefix + 'execute', { csrf, id: token }, 160000);
            stopClock();   // панель хода убрать до итога: иначе прокрутка к итогу считается с ней, и на низком экране заголовок уходит вверх
            statusResult(data.result);
            if (data.result.status === 'complete' && io.done) io.done();
        } catch (error) {
            if (responseLost(error)) {
                $('dpChainSummary').textContent = labels.unknown; $('dpChainHint').textContent = mayStillRun; return;
            }
            const noWrite = error.data && error.data.no_write_dispatched === true;
            $('dpChainSummary').textContent = noWrite ? 'Այս փորձով գրանցում չի կատարվել' : 'Արդյունքը պետք է ստուգել';
            $('dpChainHint').textContent = noWrite ? 'Շարունակելու համար բացեք նոր նախադիտում։'
                : 'Սեղմեք «Ստուգել արդյունքը»։ Նորից գրանցում չի կատարվի։'; throw error;
        } finally { stopClock(); }
    }));
    $('dpChainCheck').addEventListener('click', () => run(() => check(token)));
    $('dpChainReconcilePreview').addEventListener('click', () => run(async () => {
        reviewId = null; $('dpChainReconcile').hidden = true;
        const data = await io.read('POST', prefix + 'reconcile-preview', { csrf, id: token }, 160000);
        const result = data.result;
        reviewId = result.review_id;
        $('dpChainSummary').textContent = result.missing_count ? `Բացակայում է ${result.missing_count} վաճառք` : 'Փաստաթղթերը կան․ անհրաժեշտ է հաստատել համադրումը';
        $('dpChainBody').replaceChildren(table(['Պատվեր', 'Տրամադրում', 'Գումար'], result.missing_sales.map(row => [row.number, row.providing_number, amount(row.sum) + ' ֏'])));
        $('dpChainHint').textContent = 'Տրամադրումները գրանցված են։ Կստեղծվեն միայն բացակայող վաճառքները։ Գոյություն ունեցող փաստաթղթերը չեն կրկնվի։ Նշեք ստուգման պատճառը և հաստատեք։';
        $('dpChainReconcileReason').value = ''; $('dpChainReconcileFields').hidden = false;
        $('dpChainReconcilePreview').hidden = true; $('dpChainReconcile').hidden = false;
        $('dpChainReconcileReason').focus();
    }));
    $('dpChainReconcileReason').addEventListener('input', controls);
    $('dpChainReconcile').addEventListener('click', () => run(async () => {
        if (!reviewId) return;
        const selected = reviewId; reviewId = null; $('dpChainReconcile').hidden = true;
        try {
            const data = await io.write('POST', prefix + 'reconcile', { csrf, review_id: selected, reason: $('dpChainReconcileReason').value }, 160000);
            statusResult(data.result);
            if (data.result.status === 'complete' && io.done) io.done();
        } catch (error) {
            if (responseLost(error)) {
                $('dpChainSummary').textContent = 'Լրացման արդյունքը պետք է ստուգել'; $('dpChainHint').textContent = mayStillRun;
                $('dpChainReconcileFields').hidden = true; return;
            }
            $('dpChainSummary').textContent = 'Լրացման արդյունքը պետք է ստուգել';
            $('dpChainHint').textContent = 'Նախորդ փաստաթղթերը պահպանված են։ Սեղմեք «Ստուգել արդյունքը»։';
            $('dpChainReconcileFields').hidden = true; throw error;
        }
    }));
    $('dpChainClose').addEventListener('click', () => { if (!busy) $('dpChainDlg').close(); });
    $('dpChainDlg').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    $('dpChainDlg').addEventListener('close', () => $('dpWsMore').focus());
    window.RouteErpChain = { open };
})();

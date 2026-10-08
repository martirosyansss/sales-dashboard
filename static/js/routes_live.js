/* «Մեքենաները առցանց» /routes/live — машины на карте сейчас (ответ владельца №76, docs/plans/live-map-plan.md).
   API: GET /api/routes/live?date= (все машины, карточки без трека) и GET /api/routes/live/truck?car=&date= (выбранная:
   путь за день, магазины со статусами, журнал тревог). Расчёты — на сервере (route_optimizer/live.py); здесь только показ.
   Опрос — раз в 15 с и только пока вкладка видна. Всё, что пришло с сервера, выводится только через textContent.
   «Не успеет» (№87, t.late — live.late_forecast): в списке — красная строка, в карточке — среди тревог и у магазинов.
   Как телематика (08.10): нет связи — последнее известное положение без прогноза (t.forecast false, next.eta_unknown) и
   «данные до HH:MM»; GPS-визиты магазинов (s.gps, s.unmarked — был по GPS, водитель не отметил); «Խնդիրներ հիմա» —
   только то, что требует внимания; воспроизведение дня по t.track_t (своя отрисовка, опрос её не трогает).
   Плановая линия и отклонение (владелец 08.10): t.route — что водитель получил (отправленный план «Развоза»), коридором,
   номера магазинов — место в плане (s.plan_no, route.points); t.deviation.runs — отклонения дальше порога (красным);
   «Օրվա ցուցանիշներ» — t.stats (максимальная скорость — кнопка к точке на карте). Нет данных — «տվյալ չկա», не нули.
   «Профессионально» (владелец 08.10): t.sequence — пропущенные магазины рейса (тревога sequence), t.detour — перепробег
   по участкам фактического порядка («Ավելորդ վազք», таблица участков, оранжевым на карте), t.deviation.adherence_pct —
   следование плану; отклонение с малым перепробегом — «փոքր շեղում» (серым, тонкой линией, не в «Խնդիրներ հիմա»);
   «Բացատրել» (только администратор, data.can_explain; сервер проверяет сам) — причина и заметка в диалоге <dialog>,
   объяснённая тревога — серой с причиной, «Չեղարկել» снимает объяснение.
   Тревоги (08.10, «диспетчер сразу видит проблемы»; Samsara/Geotab/Wialon, ISA-101/ISA-18.2): важность — на клиенте
   (alarmSev): 1 красная «կրիտիկական» (треугольник) — speed, center, gps, sequence, late к окну приёма или возврату на склад;
   2 жёлтая «զգուշացում» (круг) — stop, deviation, no_contact (терминалы без мобильного интернета — частая, красной была бы
   «усталость от тревог»), late только к плану; 3 сведения (фиолетовая, не мигает) — stores.unmarked и смены порядка
   водителем из-за срока (№93, t.reorders reason until); неизвестный вид — 1.
   Активная и не отмеченная «Տեսա» — новая: мигает (1 Гц, только CSS), баннер, «(N) ⚠» во вкладке, звук (по желанию).
   «Տեսա» — по случаю: t.alerts.since (начало идущей тревоги вида, сервер); дребезг до 10 мин — тот же случай.
   Подробно — в блоке «тревоги: важность и «Տեսա»» ниже. */
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
    const OFFLINE_WHY = { closed: 'հավելվածը փակվել է', shutdown: 'հեռախոսն անջատվել է' };   // t.offline_reason (APK 2.2.5)
    const stateLabel = (t, label) => (t.state === 'offline' && Object.hasOwn(OFFLINE_WHY, t.offline_reason) ? label + ' · ' + OFFLINE_WHY[t.offline_reason] : label);
    const ALERT = {
        speed: ['fa-gauge-high', 'Արագության գերազանցում'], stop: ['fa-square-parking', 'Երկար կանգառ ոչ խանութում'],
        no_contact: ['fa-tower-broadcast', 'Կապ չկա'], gps: ['fa-location-crosshairs', 'GPS-ն անջատված է'],
        center: ['fa-ban', 'Փոքր կենտրոնում (մուտքը թույլատրված չէ)'],
        late: ['fa-hourglass-half', 'Չի հասցնում ժամանակին'], deviation: ['fa-route', 'Շեղում երթուղուց'],
        sequence: ['fa-shuffle', 'Խանութներ բաց են թողնված'],
    };
    // «Բացատրել»: причины (store.LIVE_EXPLAIN_REASONS) и какие тревоги объясняются
    const REASON = { refuel: 'Լիցքավորում', repair: 'Վերանորոգում', customer: 'Հաճախորդի խնդրանքով',
        road: 'Փակ ճանապարհ / խցանում', other: 'Այլ' };
    const EXPLAINS = ['deviation', 'sequence'];
    const REORDER_TITLE = 'Վարորդը փոխեց հերթը՝ ժամկետի պատճառով';   // №93: t.reorders с reason until — сведение
    const canExplain = () => !!(state.data && state.data.can_explain);
    // магазин в тексте: «№11 Название» (номер — место в плане машины за день)
    const storeName = (x) => [x.no ? '№' + x.no : null, x.name || x.stop_id].filter(Boolean).join(' ');
    const amd = (v) => (num(v) === null ? null : '≈ ' + fmt(v) + ' ֏');
    // №87: «не успеет» — на сколько позже окна приёма или плана; машина — возврат на склад после конца рабочего дня
    const lateText = (x) => (x.late_kind === 'return' ? 'Չի հասցնում վերադառնալ պահեստ՝ +' + x.over_min + ' րոպե'
        : (x.late_kind === 'window' ? 'Կուշանա պատուհանից ' : 'Կուշանա պլանից ') + x.over_min + ' րոպեով');
    const lateSummary = (late) => {
        const stores = (late || []).filter(x => x.late_kind !== 'return').length, back = (late || []).some(x => x.late_kind === 'return');
        return [stores ? stores + ' խանութ ուշանում է' : null, back ? 'չի հասցնում վերադառնալ' : null].filter(Boolean).join(' · ');
    };
    const STORE = { full: ['առաքված', '#45d98f'], covered: ['առաքված', '#45d98f'], partial: ['մասնակի', '#ffb547'],
        refused: ['մերժված', '#ff6b79'], in_progress: ['ընթացքի մեջ', '#38bdf8'], pending: ['դեռ ոչ', '#8693a5'],
        unmarked: ['GPS-ով այցելած, չնշված', '#a78bfa'], here: ['տեղում', '#38bdf8'] };
    const DONE = ['full', 'covered', 'partial', 'refused'];
    // точка, где машина была по GPS и уехала, а водитель не отметил (s.unmarked), — свой цвет
    const storeOf = (s) => STORE[s.unmarked ? 'unmarked' : s.status] || STORE.pending;
    const NET = { wifi: 'Wi-Fi', cell: 'բջջային', none: 'չկա' };
    const GPS = { on: 'միացված', off: 'անջատված', no_permission: 'թույլտվություն չկա' };

    const state = { date: '', data: null, detail: null, selected: null, timer: null, busy: false, again: false, fitted: false,
        map: null, markers: new Map(), layer: null, arrows: null, arrowLine: null, zone: null, lateOpen: false, pin: null, showPlan: loadPlanToggle(),
        // воспроизведение дня: снимок машины на начало (опрос его не меняет), момент t (секунды эпохи), слои
        replay: { on: false, playing: false, t: 0, raf: 0, last: 0, truck: null, prefix: null, ghost: null, stops: [] },
        // подсказка точки линии: подсказка и точка на карте; at/car — где курсор (координаты: после опроса — снова), fns — линии
        hover: { tip: null, mark: null, at: null, car: null, fns: [] },
        // тревоги: проблемы сегодня, отметки «Տեսա», уже прозвучавшие красные и объявленные новые, звук
        alarm: { probs: [], acks: loadAcks(), heard: new Set(), told: new Set(), sound: loadSound(), audio: null } };

    // «Պլանային երթուղի» на карте — удобство одного зрителя: помнится в браузере (нет хранилища — просто включено)
    function loadPlanToggle() { try { return window.localStorage.getItem('lv.plan') !== '0'; } catch (e) { return true; } }
    function savePlanToggle(on) { try { window.localStorage.setItem('lv.plan', on ? '1' : '0'); } catch (e) { /* нет хранилища */ } }

    // ---------- тревоги: важность и «Տեսա» ----------
    // Проблема — ключ «дата|машина|вид» («не успеет» — по важности: late:window / late:plan) и случай — t.alerts.since[вид]
    // (начало идущей тревоги, сервер; у «не успеет» и GPS-визитов его нет). Новая — активная (sev 1–2) без «Տեսա»: мигает
    // строка «Խնդիրներ հիմա», машина в списке, кольцо маркера (поднят над остальными), баннер над страницей, «(N) ⚠» во
    // вкладке. «Տեսա» (кнопка, нажатие на строку — оператор её увидел, «Տեսա բոլորը») — ровный цвет, пока проблема идёт.
    // Отметка (lv.ack: ключ → {since, seen — когда проблему видели последний раз}) держится, пока случай тот же (since
    // совпадает) или проблему видели не дольше ACK_GRACE_MS назад (дребезг того же случая: since обновляется); без проблемы
    // дольше ACK_GRACE_MS — снимается (вернётся — снова мигает). «Не успеет» к окну → к плану — та же отметка (ослабление),
    // к плану → к окну — новая (усиление). Две вкладки: перед записью — слияние с хранилищем (свежее seen побеждает).
    // Тревоги — только для ответа за сегодня (прошлый день и устаревший ответ при смене даты — нет; отметки не трогаются).
    // Звук (lv.sound, по умолчанию выключен): два коротких тона WebAudio один раз на каждую новую красную; AudioContext —
    // только после жеста (после перезагрузки — первое касание или клавиша на странице; до того — подсказка на кнопке, тона
    // не ставятся, без очереди). Со звуком опрос идёт и в скрытой вкладке — раз в 60 с (schedule). Мигание — только CSS
    // (1 Гц; prefers-reduced-motion: reduce — без анимации: обводка и «ՆՈՐ»); слои карты по таймеру не перерисовываются.
    const SEV = { 1: ['fa-triangle-exclamation', 'կրիտիկական'], 2: ['fa-circle-exclamation', 'զգուշացում'],
        3: ['fa-location-dot', 'տեղեկություն'] };
    const WARN_KINDS = ['stop', 'deviation', 'no_contact'];
    // важность вида тревоги; «не успеет» — к окну приёма или возврату на склад — красная, только к плану — жёлтая
    const alarmSev = (kind, late) => (kind === 'late'
        ? ((late || []).some(x => x.late_kind === 'window' || x.late_kind === 'return') ? 1 : 2)
        : (WARN_KINDS.includes(kind) ? 2 : 1));
    const isNew = (p) => p.sev < 3 && !state.alarm.acks.has(p.key);
    // порядок: новые красные, новые жёлтые, отмеченные красные, отмеченные жёлтые, сведения; внутри — номер машины
    const alarmRank = (p) => (isNew(p) ? p.sev - 1 : p.sev + 1);
    const byAlarm = (a, b) => alarmRank(a) - alarmRank(b) || a.t.car_code.localeCompare(b.t.car_code);
    const BASE_TITLE = document.title;
    const HIDDEN_POLL_MS = 60000;
    const ACK_GRACE_MS = 10 * 60000;
    const SOUND_TITLE = 'Ձայնային ազդանշան՝ նոր կրիտիկական խնդրի դեպքում';
    const SOUND_LOCKED = 'Սեղմեք էջի վրա՝ ձայնը միացնելու համար';

    function loadAcks() {
        try {
            const v = JSON.parse(window.localStorage.getItem('lv.ack') || '{}');
            const ok = (a) => !!a && typeof a.seen === 'number' && (a.since === null || typeof a.since === 'string');
            return new Map(v && typeof v === 'object' && !Array.isArray(v) ? Object.entries(v).filter(([, a]) => ok(a)) : []);
        } catch (e) { return new Map(); }
    }
    function saveAcks() { try { window.localStorage.setItem('lv.ack', JSON.stringify(Object.fromEntries(state.alarm.acks))); } catch (e) { /* нет хранилища */ } }
    // другая вкладка могла отметить своё: из хранилища — записи новее своих
    function mergeAcks() {
        for (const [k, a] of loadAcks()) {
            const mine = state.alarm.acks.get(k);
            if (!mine || a.seen > mine.seen) state.alarm.acks.set(k, a);
        }
    }
    function loadSound() { try { return window.localStorage.getItem('lv.sound') === '1'; } catch (e) { return false; } }
    function saveSound(on) { try { window.localStorage.setItem('lv.sound', on ? '1' : '0'); } catch (e) { /* нет хранилища */ } }

    // проблемы машины сегодня: тревоги (кроме «нет связи» и «не успеет») → нет связи → не успеет → не отмеченные GPS-визиты
    function problemsOf(t) {
        const since = (t.alerts && t.alerts.since) || {};
        const one = (kind, sev, title, text, ex) => ({ key: state.data.date + '|' + t.car_code + '|' + kind, kind, t, sev, title, text,
            since: typeof since[kind] === 'string' ? since[kind] : null, ex: ex || null });
        const out = [];
        for (const k of t.alerts.active || []) {
            if (k === 'late' || k === 'no_contact') continue;
            // отклонение и порядок объезда — с кнопкой «Բացատրել» (администратор): тревога — из t.explainable
            const ex = EXPLAINS.includes(k) && canExplain() ? (t.explainable || []).find(x => x.kind === k) : null;
            const title = (ALERT[k] || ['', k])[1];
            out.push(one(k, alarmSev(k), title, title, ex));
        }
        if (noContact(t)) {
            out.push(one('no_contact', alarmSev('no_contact'), ALERT.no_contact[1],
                ('Կապ չկա ' + silentFor(silentAge(t))).trim() + (t.data_until ? ' · վերջինը՝ ' + hm(t.data_until) : '')));
        }
        if (lateSummary(t.late)) {
            const sev = alarmSev('late', t.late);
            out.push(one(sev === 1 ? 'late:window' : 'late:plan', sev, ALERT.late[1], lateSummary(t.late)));
        }
        if (t.stores.unmarked) {
            out.push(one('unmarked', 3, 'GPS-ով այցելած, չնշված', t.stores.unmarked + ' խանութ GPS-ով այցելած է, բայց չնշված'));
        }
        // №93: водитель сам поставил магазин со сроком под риском первым («Գնալ առաջինը», reason until) — сведение, не тревога
        const moved = (t.reorders || []).filter(r => r.reason === 'until' && r.moved);
        if (moved.length) {
            out.push(one('reorder', 3, REORDER_TITLE, REORDER_TITLE + '՝ ' + moved.map(r => storeName(r.moved) + ' (' + hm(r.at) + ')').join(', ')));
        }
        return out;
    }

    // отметка проблемы сейчас: тот же случай (since) или дребезг в пределах ACK_GRACE_MS (без since — только он);
    // «не успеет» к плану — и отметка «к окну»
    function ackOf(p, now) {
        const acks = state.alarm.acks;
        const a = acks.get(p.key) || (p.kind === 'late:plan' ? acks.get(p.key.replace(/late:plan$/, 'late:window')) : null);
        return a && ((p.since !== null && a.since === p.since) || now - a.seen <= ACK_GRACE_MS) ? a : null;
    }

    // после опроса: проблемы, отметки, звук на новые красные, объявление новых для экранного диктора
    function syncAlarms(data) {
        const al = state.alarm;
        if (state.date || data.date !== String(data.now || '').slice(0, 10)) { al.probs = []; return; }
        const now = Date.now();
        al.probs = data.trucks.flatMap(problemsOf);
        mergeAcks();
        for (const p of al.probs) {
            if (ackOf(p, now)) al.acks.set(p.key, { since: p.since, seen: now }); else al.acks.delete(p.key);
        }
        const keys = new Set(al.probs.map(p => p.key));
        for (const [k, a] of al.acks) if (!keys.has(k) && now - a.seen > ACK_GRACE_MS) al.acks.delete(k);
        saveAcks();
        const fresh = al.probs.filter(isNew), red = fresh.filter(p => p.sev === 1).map(p => p.key);
        if (al.sound && red.some(k => !al.heard.has(k))) beep();
        al.heard = new Set(red);
        // диктор — только когда новых прибавилось (не каждые 15 с)
        if (fresh.some(p => !al.told.has(p.key))) $('lvAlarmSr').textContent = alarmText(fresh);
        al.told = new Set(fresh.map(p => p.key));
    }

    const alarmText = (fresh) => fresh.length + ' նոր խնդիր՝ ' + fresh.slice().sort(byAlarm).map(p => p.t.car_code + ' · ' + p.title).join(', ');

    // машина: наибольшая важность активных проблем и новых (0 — нет); классы строки списка и маркера
    function carAlarm(car) {
        const mine = state.alarm.probs.filter(p => p.t.car_code === car);
        const top = (ps) => (ps.length ? Math.min(...ps.map(p => p.sev)) : 0);
        return { sev: top(mine), fresh: top(mine.filter(isNew)) };
    }
    const alarmCls = (a) => [a.sev ? 'lv-sev' + a.sev : null, a.fresh ? 'is-new lv-new' + a.fresh : null].filter(Boolean).join(' ');

    function ack(keys) {
        const al = state.alarm;
        if (!keys.some(k => !al.acks.has(k))) return;
        mergeAcks();
        const now = Date.now();
        for (const k of keys) {
            const p = al.probs.find(x => x.key === k);
            al.acks.set(k, { since: p ? p.since : null, seen: now });
        }
        saveAcks();
        const trucks = state.data ? state.data.trucks : [];
        renderProblems();
        renderSummary(trucks);
        renderList(trucks);
        renderMarkers(trucks);
        renderBanner();
    }

    // баннер новых проблем и счётчик во вкладке; новых нет — и диктору нечего держать
    function renderBanner() {
        const fresh = state.alarm.probs.filter(isNew).sort(byAlarm);
        $('lvAlarm').hidden = !fresh.length;
        document.title = fresh.length ? '(' + fresh.length + ') ⚠ ' + BASE_TITLE : BASE_TITLE;
        if (!fresh.length) { $('lvAlarm').className = 'lv-alarm'; $('lvAlarmSr').textContent = ''; return; }
        const sev = fresh[0].sev;   // есть новая красная — баннер красный
        $('lvAlarm').className = 'lv-alarm is-new lv-sev' + sev + ' lv-new' + sev;
        $('lvAlarmIco').className = 'fas ' + SEV[sev][0] + ' lv-alarm-ico';
        $('lvAlarmCount').textContent = fresh.length + ' նոր խնդիր';
        const items = fresh.slice(0, 3).map(p => h('li', null, icon(SEV[p.sev][0]), h('span', { text: p.t.car_code + ' · ' + p.title })));
        if (fresh.length > 3) items.push(h('li', { text: '+' + (fresh.length - 3) }));
        $('lvAlarmList').replaceChildren(...items);
    }

    // звук: AudioContext — только по жесту пользователя (без жеста он «спит»); ошибки не наружу
    const audioReady = () => !!state.alarm.audio && state.alarm.audio.state === 'running';
    function wakeAudio() {
        try {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return Promise.resolve();
            const ctx = state.alarm.audio || (state.alarm.audio = new AC());
            ctx.onstatechange = renderSound;
            return ctx.state === 'running' ? Promise.resolve() : ctx.resume().catch(() => { /* ждёт жеста */ });
        } catch (e) { return Promise.resolve(); }
    }
    // звук был включён до перезагрузки — проснуться от жеста на странице; pointerdown на телефоне (Android Chrome, iOS
    // Safari) жестом не считается — и pointerup / click / touchend / keydown; слушать, пока звук не готов
    const WAKE_EVENTS = ['pointerdown', 'pointerup', 'click', 'touchend', 'keydown'];
    function armAudio() {
        if (!state.alarm.sound || audioReady()) return;
        const wake = () => {
            if (!state.alarm.sound) return;
            wakeAudio().then(() => {
                renderSound();
                if (audioReady()) WAKE_EVENTS.forEach(e => document.removeEventListener(e, wake, true));
            });
        };
        WAKE_EVENTS.forEach(e => document.addEventListener(e, wake, true));
    }

    // два коротких тона (без файлов); контекст «спит» — тонов не ставим (иначе пачкой после пробуждения)
    function beep() {
        if (!audioReady()) return;
        try {
            const ctx = state.alarm.audio, t0 = ctx.currentTime + 0.02;
            [[880, 0], [660, 0.17]].forEach(([hz, at]) => {
                const osc = ctx.createOscillator(), gain = ctx.createGain();
                osc.frequency.value = hz;
                gain.gain.setValueAtTime(0.0001, t0 + at);
                gain.gain.exponentialRampToValueAtTime(0.25, t0 + at + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.15);
                osc.connect(gain).connect(ctx.destination);
                osc.start(t0 + at);
                osc.stop(t0 + at + 0.16);
            });
        } catch (e) { /* звука нет — тревога видна и без него */ }
    }

    function renderSound() {
        const on = state.alarm.sound, locked = on && !audioReady(), b = $('lvSound');
        b.setAttribute('aria-pressed', String(on));
        b.classList.toggle('is-locked', locked);
        b.title = locked ? SOUND_LOCKED : SOUND_TITLE;
        b.replaceChildren(icon(on ? 'fa-volume-high' : 'fa-volume-xmark'), h('span', { text: 'Ձայն' }),
            ...(locked ? [h('span', { class: 'rt-sr-only', text: SOUND_LOCKED })] : []));
    }

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
    async function api(url, payload) {
        let resp;
        const init = { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } };
        if (payload !== undefined) {   // POST раздела — только JSON (защита от CSRF на сервере: _json_body)
            init.method = 'POST';
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(payload);
        }
        try { resp = await fetch(url, init); }
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
    // прогноза нет (live.car_view forecast: нет связи, GPS выключен, точки ещё нет) — сегодня, день не закрыт: маркер
    // серый полупрозрачный в последнем известном положении, данные карточки — «до HH:MM»
    const noForecast = (t) => !state.date && !t.closed && !t.forecast;
    // сколько машину не слышно: от последних данных (связь или точка GPS, data_until) до момента расчёта
    const silentAge = (t) => (t.data_until && state.data ? (Date.parse(state.data.now) - Date.parse(t.data_until)) / 1000 : null);
    // нет связи: прогноза нет и данных нет дольше порога «կապ չկա» (APK с device — настройка, старый APK — пачками, свой)
    const noContact = (t) => {
        const th = state.data ? state.data.thresholds : null, age = silentAge(t);
        return noForecast(t) && !!th && age !== null && age > 60 * (t.device ? th.no_contact_min : th.old_apk_silent_min);
    };
    const silentFor = (s) => {   // сколько нет связи: «N րոպե» / «N ժ M ր»
        const n = num(s);
        if (n === null) return '';
        return n < 3600 ? Math.round(n / 60) + ' րոպե' : Math.floor(n / 3600) + ' ժ ' + Math.round((n % 3600) / 60) + ' ր';
    };
    const untilText = (t) => (t.data_until ? 'տվյալները՝ մինչև ' + hm(t.data_until) : null);
    // секунды эпохи (track_t) и ISO → часы Еревана (UTC+4, без перехода на летнее время)
    const epoch = (iso) => (typeof iso === 'string' ? Date.parse(iso) / 1000 : null);
    const clockOf = (sec) => new Date((sec + 4 * 3600) * 1000).toISOString().slice(11, 19);
    const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // «возраст» точки: серый — старше 2 мин, красный — дольше порога «нет связи»
    const ageClass = (s) => {
        const n = num(s), th = state.data ? state.data.thresholds : null;
        if (n === null || !th) return 'is-mute';
        if (n > th.no_contact_min * 60) return 'is-bad';
        return n > th.stale_s ? 'is-mute' : '';
    };
    // сверка расчёта топлива с заправками (этап 2): последняя заправка, интервал «полный бак → полный бак», расчёт после неё
    const fuelCheckText = (c) => {
        if (!c) return null;
        const parts = [];
        if (c.last && c.last.liters !== null) {
            parts.push('վերջին լիցքավորում՝ ' + fmt(c.last.liters, 1) + ' լ, ' + (c.last.today ? hm(c.last.at) : c.last.at.slice(5, 10).split('-').reverse().join('.'))
                + (c.last.full ? '' : ' (ոչ լրիվ բաք)'));
        }
        if (c.since_l !== null && c.since_l !== undefined) parts.push('լիցքավորումից հետո՝ ≈ ' + fmt(c.since_l, 1) + ' լ');
        const iv = c.interval;
        if (iv) {
            parts.push('լրիվ բաքից լրիվ բաք (' + fmt(iv.km) + ' կմ)՝ լցրել են ' + fmt(iv.liters, 1) + ' լ'
                + (iv.calc_l !== null ? ', նորմով՝ ' + fmt(iv.calc_l, 1) + ' լ (' + (iv.delta_pct > 0 ? '+' : '') + fmt(iv.delta_pct, 1) + '%)' : ''));
        }
        return parts.join(' · ');
    };
    // откуда ETA: по дорожной модели (Valhalla / граф дорог, время у магазина, обед) или запасная — по прямой × извилистость
    const SRC = { road: 'ճանապարհներով', model: 'մոտավոր' };
    const srcText = (v) => (SRC[v] ? ' (' + SRC[v] + ')' : '');
    const delayText = (d) => {
        const n = num(d);
        if (n === null) return null;
        if (Math.abs(n) <= 5) return ['ժամանակին', 'is-ok'];
        return n > 0 ? ['+' + n + ' րոպե ուշացում', n > 15 ? 'is-bad' : 'is-warn'] : [Math.abs(n) + ' րոպե շուտ', 'is-ok'];
    };

    // ---------- список ----------
    function renderSummary(trucks) {
        const count = (st) => trucks.filter(t => t.state === st).length;
        // «ահազանգ» мигает, пока у машины «ահազանգ» есть новая красная («не успеет» состояния «ահազանգ» не даёт)
        const redNew = state.alarm.probs.some(p => p.sev === 1 && isNew(p) && p.t.state === 'alert');
        $('lvSummary').replaceChildren(
            ...[['moving', 'ընթացքում'], ['standing', 'կանգնած'], ['alert', 'ահազանգ'], ['offline', 'կապ չկա']]
                .map(([st, label]) => h('div', { class: 'lv-sum is-' + st + (st === 'alert' && redNew ? ' is-new lv-new1' : '') },
                    h('b', { text: String(count(st)) }), h('span', { text: label }))));
    }

    function renderList(trucks) {
        $('lvEmpty').hidden = trucks.length > 0;
        const list = $('lvList');
        list.replaceChildren(...trucks.map(t => {
            const [baseLabel, cls] = STATE[t.state] || STATE.standing;
            const label = stateLabel(t, baseLabel);
            const pos = t.position;
            const meta = [h('span', { text: 'Խանութներ՝ ' + t.stores.done + '/' + t.stores.total
                + (t.stores.gps_visited ? ' · GPS-ով՝ ' + t.stores.gps_visited : '') })];
            if (pos) meta.push(h('span', { class: ageClass(pos.age_s), text: ago(pos.age_s) }));
            // нет связи — не опоздание «как будто машина ещё там», а сколько её не слышно
            if (noContact(t)) meta.push(h('span', { class: 'is-bad', text: ('կապ չկա ' + silentFor(silentAge(t))).trim() }));
            else if (t.next && t.next.delay_min !== null) {
                const d = delayText(t.next.delay_min);
                if (d) meta.push(h('span', { class: d[1] === 'is-ok' ? '' : (d[1] === 'is-bad' ? 'is-bad' : 'is-warn'), text: d[0] }));
            }
            if (lateSummary(t.late)) meta.push(h('span', { class: 'is-bad', text: lateSummary(t.late) }));
            // тревоги: полоса слева и фон цвета важности (новая — мигает), значок важности у номера (не только цвет)
            const a = carAlarm(t.car_code);
            const item = h('li', { class: ('lv-item ' + alarmCls(a)).trim(), role: 'option', tabindex: '0', 'aria-selected': String(t.car_code === state.selected),
                'data-car': t.car_code },
                h('span', { class: 'lv-dot ' + cls, title: label }),
                h('div', { class: 'lv-item-main' }, h('div', { class: 'lv-item-plate' }, t.car_code,
                    a.sev ? h('i', { class: 'fas ' + SEV[a.sev][0] + ' lv-sev-ico', 'aria-hidden': 'true', title: SEV[a.sev][1] }) : null,
                    a.sev ? h('span', { class: 'rt-sr-only', text: SEV[a.sev][1] + (a.fresh ? ', նոր' : '') }) : null),
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

    // «Խնդիրներ հիմա» (сегодня): проблемы state.alarm.probs по важности (byAlarm); строка — выбор машины (и «Տեսա»)
    function renderProblems() {
        const box = $('lvProbs');
        box.hidden = !!state.date;
        if (state.date) return;
        const list = $('lvProbList');
        // фокус клавиатуры в списке — после перерисовки на ту же строку; «Տեսա» исчезла — на саму строку
        const was = list.contains(document.activeElement) ? document.activeElement : null;
        const focusKey = was && was.closest('li') ? was.closest('li').dataset.key : null;
        const focusCls = was ? ['lv-ack', 'lv-explain'].find(c => was.classList.contains(c)) || 'lv-prob' : null;
        const rows = state.alarm.probs.slice().sort(byAlarm);
        list.replaceChildren(...(rows.length ? rows.map(problemRow)
            : [h('li', null, h('div', { class: 'lv-prob is-none' }, icon('fa-circle-check'), h('span', { text: 'Խնդիրներ չկան' })))]));
        const li = focusKey ? [...list.children].find(x => x.dataset.key === focusKey) : null;
        const to = li ? li.querySelector('.' + focusCls) || li.querySelector('.lv-prob') : null;
        if (to) to.focus({ preventScroll: true });
    }

    function problemRow(p) {
        const car = p.t.car_code, fresh = isNew(p);
        const btn = h('button', { type: 'button', class: 'lv-prob lv-sev' + p.sev + (fresh ? ' is-new lv-new' + p.sev : '') },
            icon(SEV[p.sev][0]), h('b', { text: car }), h('span', { text: p.text }),
            h('span', { class: 'rt-sr-only', text: SEV[p.sev][1] + (fresh ? ', նոր' : '') }),
            fresh ? h('em', { class: 'lv-new-tag', 'aria-hidden': 'true', text: 'ՆՈՐ' }) : null);
        // нажатие на строку — оператор проблему увидел
        btn.addEventListener('click', () => {
            if (fresh) ack([p.key]);
            if (state.selected !== car) select(car); else focusCard();
        });
        let seen = null;
        if (fresh) {
            seen = h('button', { type: 'button', class: 'lv-ack', 'aria-label': 'Տեսա՝ ' + car + ' · ' + p.title },
                icon('fa-check'), h('span', { text: 'Տեսա' }));
            seen.addEventListener('click', () => ack([p.key]));
        }
        return h('li', { class: seen || p.ex ? 'lv-prob-row' : null, 'data-key': p.key }, btn, seen,
            p.ex ? explainButton(car, p.ex, car + ' · ' + p.text) : null);
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
        state.map.createPane('lvArrows').style.zIndex = 590;   // стрелки пути: над линиями (400), под значками (600)
        state.arrows = L.layerGroup().addTo(state.map);
        state.map.on('zoomend', drawArrows);
        state.map.on('click', forgetHover);   // касание мимо линии на телефоне — подсказку убрать
        state.map.on('mouseout', forgetHover);   // курсор ушёл с карты (линию перерисовал опрос — её mouseout не придёт)
    }

    // нет связи — серый полупрозрачный маркер в последнем известном положении и «վերջինը՝ HH:MM» (время этой точки GPS:
    // при выключенном GPS связь свежая, а положение — давнее) под номером
    const lastFix = (t) => (t.position ? hm(t.position.at) : hm(t.data_until));
    const stale = (t) => t.state === 'offline' || t.state === 'nodata' || noForecast(t);

    function markerIcon(t) {
        const [, cls] = STATE[t.state] || STATE.standing;
        // тревоги: новая — пульсирующее кольцо цвета важности, отмеченная «Տեսա» — ровное; «ահազանգ» только из жёлтых — жёлтый
        const a = carAlarm(t.car_code);
        const el = h('div', { class: ['lv-marker', cls, t.car_code === state.selected ? 'is-selected' : null, stale(t) ? 'is-stale' : null,
            alarmCls(a), t.state === 'alert' && a.sev === 2 ? 'is-warn' : null].filter(Boolean).join(' ') });
        const heading = t.position ? num(t.position.heading) : null;
        if (heading !== null && t.state === 'moving') {
            const arrow = h('span', { class: 'lv-marker-arrow' });
            arrow.style.transform = 'rotate(' + heading + 'deg)';
            el.append(arrow);
        }
        el.append(h('span', { class: 'lv-marker-body' }, icon('fa-truck')), h('span', { class: 'lv-marker-plate', text: t.car_code }));
        if (stale(t)) el.append(h('span', { class: 'lv-marker-last', text: 'վերջինը՝ ' + lastFix(t) }));
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
            // значок не изменился — не заменять (иначе мигание кольца начинается заново каждые 15 с)
            const ic = markerIcon(t);
            if (m.lvHtml !== ic.options.html) { m.setIcon(ic); m.lvHtml = ic.options.html; }
            // выбранная — сверху, машина с новой проблемой — над остальными и над номерами магазинов (500)
            m.setZIndexOffset(t.car_code === state.selected ? 1000 : carAlarm(t.car_code).fresh ? 800 : 0);
            m.bindTooltip(tip(t.car_code + (t.name ? ' · ' + t.name : '')
                + (stale(t) ? ' · Վերջին հայտնի դիրքը՝ ' + lastFix(t) : '')), { direction: 'top', offset: [0, -16] });
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

    // GPS-визит точки: «ժամանում 10:42 · մեկնում 10:55 · 13 ր» или «տեղում է N ր»
    const gpsText = (g) => (!g ? null : g.here ? 'տեղում է ' + g.minutes + ' ր'
        : 'ժամանում ' + hm(g.arrive) + (g.leave ? ' · մեկնում ' + hm(g.leave) : '') + ' · ' + g.minutes + ' ր');

    // номер магазина в плане машины за день: цвет — состояние; без цвета — магазин плана, которого нет у терминала
    function pinIcon(no, color, late) {
        const el = h('span', { class: 'lv-npin' + (color ? '' : ' is-plan-only') + (late ? ' is-late' : ''), text: String(no) });
        if (color) el.style.background = color;
        return L.divIcon({ html: el.outerHTML, className: '', iconSize: [24, 24], iconAnchor: [12, 12] });
    }

    // ---------- подсказка точки линии (владелец 08.10: «при наведении на линию покажи данные на этой точке») ----------
    // Факт: t.track_v (км/ч; 0 — стоянка; null — данных нет) и t.track_km — 1:1 с t.track; t.track_dev_m — [индекс, м]
    // только у точек внутри отклонения (-1 — дальше 5 км). Стоянка — две одинаковые точки линии подряд (track_line): где —
    // GPS-визит магазина в это время, иначе магазин дня или склад в радиусе (thresholds); перерыв трека (между соседними
    // точками дольше GAP_S без точек трека внутри — t.track_gaps) — «տվյալ չկա». План: t.route.trip_nos — номера магазинов каждой линии рейса, trip_cuts —
    // индексы концов участков в ней (склад, магазины, склад). Мышь — над линией (невидимая широкая линия поверх), телефон —
    // касание; подсказка — у ближайшей к курсору линии выбранной машины (путь или план), точка — по экрану (ближайший
    // участок), момент и км — между соседними точками.
    const HOVER_PX = 24;   // курсор дальше от линии — подсказки нет (после опроса — снова, только если линия рядом)
    const STAY_PX = 14;    // у точки стоянки ближе — подсказка стоянки (номер магазина на карте — 12 px радиус)
    const TRACK_PX = 2;    // путь и план рядом — у пути преимущество в 2 px (план — где курсор явно ближе к нему)
    const hmOf = (sec) => clockOf(sec).slice(0, 5);
    const metres = (a, b) => Math.hypot((b[0] - a[0]) * 110540, (b[1] - a[1]) * 111320 * Math.cos(a[0] * Math.PI / 180));
    const same = (a, b) => !!a && !!b && a[0] === b[0] && a[1] === b[1];

    function clearHover() {
        const hv = state.hover;
        if (hv.tip) hv.tip.remove();
        if (hv.mark) hv.mark.remove();
        hv.tip = hv.mark = null;
    }
    function forgetHover() { clearHover(); Object.assign(state.hover, { at: null, car: null }); }

    function showHover(ll, lines) {
        const hv = state.hover;
        const el = h('div', { class: 'lv-hover' }, ...lines.map(([text, cls]) => h('div', { class: cls || null, text })));
        if (hv.mark) hv.mark.setLatLng(ll);
        else hv.mark = L.circleMarker(ll, { radius: 6, color: '#0e1116', weight: 2, fillColor: '#ffb547', fillOpacity: 1, interactive: false }).addTo(state.map);
        if (hv.tip) hv.tip.setLatLng(ll).setContent(el);
        else hv.tip = L.tooltip({ direction: 'top', offset: [0, -8], opacity: 1, className: 'lv-hover-tip' }).setLatLng(ll).setContent(el).addTo(state.map);
    }

    // подсказка у точки карты ll (координаты, не пиксели: после опроса и зума — та же точка): ближайшая линия из hover.fns
    function hoverAt(ll) {
        const hv = state.hover;
        if (!state.map || !ll) return;
        const p = state.map.latLngToLayerPoint(ll);
        let best = null;
        for (const fn of hv.fns) { const x = fn(p); if (x && (!best || x.d < best.d)) best = x; }
        if (!best) { forgetHover(); return; }
        Object.assign(hv, { at: ll, car: state.selected });
        showHover(best.ll, best.lines);
    }

    // ближайший к точке экрана p (слой карты) участок ломаной: начало i, доля f вдоль него, расстояние d и длина len, px
    function nearestSeg(line, cache, p) {
        const map = state.map, key = map.getZoom() + ':' + map.getPixelOrigin().toString();
        if (cache.key !== key) { cache.key = key; cache.pts = line.map(ll => map.latLngToLayerPoint(ll)); }
        const q = cache.pts;
        let best = { i: 0, f: 0, d: Infinity, len: 0 };
        for (let i = 0; i + 1 < q.length; i++) {
            const a = q[i], b = q[i + 1], dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
            const f = d2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / d2)) : 0;
            const d = Math.hypot(p.x - a.x - f * dx, p.y - a.y - f * dy);
            if (d < best.d) best = { i, f, d, len: Math.sqrt(d2) };
        }
        return best;
    }
    const along = (line, n) => [line[n.i][0] + n.f * (line[n.i + 1][0] - line[n.i][0]), line[n.i][1] + n.f * (line[n.i + 1][1] - line[n.i][1])];

    // где стояла машина: магазин с GPS-визитом в это время, иначе магазин дня или склад рядом, иначе — неизвестно
    function stayPlace(t, p, a, b) {
        const th = (state.data && state.data.thresholds) || {};
        const stops = t.stops || [];
        const visit = stops.find(s => s.gps && epoch(s.gps.arrive) <= b && (s.gps.leave ? epoch(s.gps.leave) : Infinity) >= a);
        if (visit) return visit.name || visit.stop_id;
        const near = stops.filter(s => num(s.lat) !== null && num(s.lon) !== null)
            .map(s => [metres(p, [s.lat, s.lon]), s]).filter(([d]) => d <= (num(th.stop_radius_m) ?? 100))
            .sort((x, y) => x[0] - y[0])[0];
        if (near) return near[1].name || near[1].stop_id;
        if (state.data && state.data.depot && metres(p, state.data.depot) <= (num(th.depot_radius_m) ?? 150)) return 'Պահեստ';
        return 'անհայտ վայր';
    }

    function trackInfo(t, n, devOf, stay, gapOf) {
        const tr = t.track, tt = t.track_t, th = (state.data && state.data.thresholds) || {};
        const one = (a) => Array.isArray(a) && a.length === tr.length;
        const i = n.i, j = i + 1, k = n.f < 0.5 ? i : j;   // k — ближайшая точка участка
        const kmAt = (x) => (one(t.track_km) && num(t.track_km[x]) !== null ? t.track_km[x] : null);
        const kmLine = (km) => (km !== null ? [['անցած՝ ' + fmt(km, 1) + ' կմ']] : []);
        // стоянка (её две точки подряд): курсор на ней или ближе STAY_PX к ней (магазин и склад — под своим значком)
        const s = same(tr[i], tr[j]) ? i : stay;
        if (s !== null) {
            const a = tt[s], b = tt[s + 1];
            return { ll: tr[s], lines: [['Կանգառ՝ ' + stayPlace(t, tr[s], a, b)],
                [hmOf(a) + '–' + hmOf(b) + ' · ' + Math.max(0, Math.round((b - a) / 60)) + ' րոպե'], ...kmLine(kmAt(s))] };
        }
        if (gapOf.has(i)) {   // перерыв трека (сервер: без точек внутри; стоял в пробке — не перерыв): что было — неизвестно
            return { ll: along(tr, n), lines: [['տվյալ չկա'], [hmOf(tt[i]) + '–' + hmOf(tt[j]) + ' · GPS կետեր չկան', 'is-mute']] };
        }
        const x = tt[i] + n.f * (tt[j] - tt[i]);
        const lines = [['ժամը ' + hmOf(x)]];
        const v = one(t.track_v) ? num(t.track_v[k]) : null;
        const over = v !== null && num(th.speed_kmh) !== null && v > th.speed_kmh;
        lines.push(v === null ? ['արագություն՝ տվյալ չկա', 'is-mute'] : v === 0 ? ['կանգնած']
            : ['արագություն՝ ' + v + ' կմ/ժ' + (over ? ' · գերազանցում (> ' + fmt(th.speed_kmh) + ')' : ''), over ? 'is-bad' : null]);
        const ka = kmAt(i), kb = kmAt(j);
        lines.push(...kmLine(ka !== null && kb !== null ? ka + n.f * (kb - ka) : null));
        const dev = devOf.has(k) ? devOf.get(k) : devOf.get(k === i ? j : i);
        if (num(dev) !== null) {   // до плановой линии: метры, от километра — км
            lines.push(['Շեղում երթուղուց՝ ' + (dev < 0 ? '5 կմ-ից ավելի' : dev < 1000 ? fmt(dev) + ' մ' : fmt(dev / 1000, 1) + ' կմ'), 'is-bad']);
        }
        // отклонение целиком (его красная линия — под линией подсказки): км и время
        const run = ((t.deviation && t.deviation.runs) || []).find(r => epoch(r.from) <= x && (r.to ? epoch(r.to) >= x : true));
        if (run) lines.push(['շեղումը՝ ' + fmt(run.km, 1) + ' կմ · ' + hm(run.from) + (run.to ? '–' + hm(run.to) : ' — հիմա'), 'is-bad']);
        return { ll: along(tr, n), lines };
    }

    function trackHover(t) {
        const cache = { key: '', pts: [] };
        const devOf = new Map((Array.isArray(t.track_dev_m) ? t.track_dev_m : []).filter(x => Array.isArray(x) && x.length === 2));
        const stays = t.track.map((p, i) => i).filter(i => same(t.track[i], t.track[i + 1]));
        const gapOf = new Set(Array.isArray(t.track_gaps) ? t.track_gaps : []);
        return (p) => {
            const n = nearestSeg(t.track, cache, p);
            if (n.d > HOVER_PX) return null;
            let stay = null, best = STAY_PX;   // ближайшая к курсору точка стоянки на экране
            for (const i of stays) { const q = cache.pts[i], d = Math.hypot(q.x - p.x, q.y - p.y); if (d <= best) { best = d; stay = i; } }
            return { d: n.d - TRACK_PX, ...trackInfo(t, n, devOf, stay, gapOf) };
        };
    }

    // участок плана: «Երթ N · A → B» и его км по линии; концы участков — trip_cuts сервера (склад, магазины, склад)
    function planHover(t, li) {
        const r = t.route, line = r.lines[li], cache = { key: '', pts: [] };
        const how = 'Պլանային երթուղի (' + (r.road ? 'ճանապարհներով' : 'ուղիղ գծով') + ')';
        const nos = Array.isArray(r.trip_nos) && r.trip_nos.length === r.lines.length ? r.trip_nos[li] : null;
        const cuts = Array.isArray(r.trip_cuts) && r.trip_cuts.length === r.lines.length ? r.trip_cuts[li] : null;
        const nameOf = (no) => { const s = (t.stops || []).find(x => x.plan_no === no); return '№' + no + (s && s.name ? ' ' + s.name : ''); };
        // концы: склад и магазины по порядку (линия — склад → магазины → склад; склада нет — только магазины); не сходится — без участка
        const names = !nos || !cuts || !nos.length ? null : cuts.length === nos.length + 2 ? ['Պահեստ', ...nos.map(nameOf), 'Պահեստ']
            : cuts.length === nos.length ? nos.map(nameOf) : null;
        const anchors = names ? cuts.map((v, k) => [v, names[k]]) : [];
        return (p) => {
            const n = nearestSeg(line, cache, p);
            if (n.d > HOVER_PX) return null;
            const title = 'Երթ ' + (li + 1);
            if (anchors.length < 2) return { d: n.d, ll: along(line, n), lines: [[title], [how, 'is-mute']] };
            const pos = n.i + n.f;
            let m = 0;
            while (m + 2 < anchors.length && anchors[m + 1][0] <= pos) m++;
            const [va, a] = anchors[m], [vb, b] = anchors[m + 1];
            let km = 0;
            for (let v = va; v < vb; v++) km += metres(line[v], line[v + 1]) / 1000;
            return { d: n.d, ll: along(line, n), lines: [[title + ' · ' + a + ' → ' + b], ['հատվածը՝ ≈ ' + fmt(km, 1) + ' կմ'], [how, 'is-mute']] };
        };
    }

    // невидимая широкая линия поверх: мышь — подсказка следует за курсором, касание — подсказка в точке касания
    function hoverable(line, cls, info, layer) {
        state.hover.fns.push(info);
        L.polyline(line, { color: '#000', opacity: 0, weight: 20, lineCap: 'round', className: 'lv-hit ' + cls })
            .on('mousemove', (e) => hoverAt(e.latlng))
            .on('click', (e) => { L.DomEvent.stopPropagation(e); hoverAt(e.latlng); })
            .on('mouseout', forgetHover)
            .addTo(layer);
    }

    // ---------- линии на карте (владелец 08.10: «не информативные и не красивые, как делают гиганты») ----------
    // Подложка — светлый Яндекс (тёмный фильтр — только у запасного OSM): у линий белая обводка (casing), как у
    // навигаторов, — читаются на любой подложке. План — широкий полупрозрачный «коридор», факт — насыщенная линия со
    // стрелками направления поверх; отклонение — тот же факт другим цветом (не толще), чтобы день не тонул в красном.
    const LN = { track: '#1a73e8', plan: '#7c4dff', dev: '#d93025', minor: '#f57c00', explained: '#80868b',
        over: '#d93025', casing: '#ffffff' };   // ореол перепробега — не жёлтый: жёлтые у Яндекса трассы
    const LINE_W = 5;      // факт и отклонения; обводка — на 4 px шире
    const ARROW_PX = 90;   // шаг стрелок направления на экране

    // линия с белой обводкой: обводка не ловит мышь, cls — у цветной линии (её ищет проверка в браузере)
    function cased(line, color, cls, layer, opts) {
        const casing = L.polyline(line, { color: LN.casing, weight: LINE_W + 4, opacity: 1, interactive: false, className: 'lv-l-casing' }).addTo(layer);
        return Object.assign(L.polyline(line, { color, weight: LINE_W, opacity: 1, className: cls, ...opts }).addTo(layer), { casing });
    }

    // стрелки направления по фактическому пути: каждые ARROW_PX на экране, пересчёт при смене масштаба; своя панель
    // под значками магазинов, без мыши (подсказка точки линии — по линии под ними)
    function setArrows(line) {
        state.arrowLine = Array.isArray(line) && line.length > 1 ? line : null;
        drawArrows();
    }

    function drawArrows() {
        const g = state.arrows, line = state.arrowLine;
        if (!g) return;
        g.clearLayers();
        if (!line) return;
        const m = state.map;
        let prev = m.latLngToLayerPoint(line[0]), acc = ARROW_PX / 2, n = 0;
        for (let i = 1; i < line.length && n < 400; i++) {
            const cur = m.latLngToLayerPoint(line[i]), dx = cur.x - prev.x, dy = cur.y - prev.y, len = Math.hypot(dx, dy);
            for (; acc <= len && n < 400; acc += ARROW_PX, n++) {
                const at = m.layerPointToLatLng(L.point(prev.x + dx * acc / len, prev.y + dy * acc / len));
                const el = h('span', { class: 'lv-arrow' });
                el.style.transform = 'rotate(' + Math.round(Math.atan2(dy, dx) * 180 / Math.PI) + 'deg)';
                L.marker(at, { icon: L.divIcon({ html: el.outerHTML, className: '', iconSize: [14, 14], iconAnchor: [7, 7] }),
                    pane: 'lvArrows', interactive: false, keyboard: false }).addTo(g);
            }
            acc -= len;
            prev = cur;
        }
    }

    // начало пути за день: белая точка с обводкой цвета пути и временем выезда
    function drawStart(t, layer) {
        if (!t.track || t.track.length < 2) return;
        const at = Array.isArray(t.track_t) && t.track_t.length ? ' · ' + clockOf(t.track_t[0]).slice(0, 5) : '';
        L.circleMarker(t.track[0], { radius: 6, color: LN.track, weight: 3, fillColor: '#ffffff', fillOpacity: 1, className: 'lv-l-start' })
            .bindTooltip(tip('Ճանապարհի սկիզբը' + at)).addTo(layer);
    }

    // ---------- стоянки не по плану (владелец 08.10 «очень чётко покажи, где были остановки вне маршрута») ----------
    // t.stops_off (live.off_stays): ≥ 5 мин вне склада и точек дня. На карте — плашка с минутами поверх всего: янтарная —
    // короткая, красная — длиннее порога тревоги (long), серая с вилкой — обед; красное кольцо — вне плановой линии
    // (off_line), пульс — стоит там сейчас. В карточке — таблица «Կանգառներ ոչ խանութում» с кнопкой к точке на карте.
    const offStops = (t) => (Array.isArray(t.stops_off) ? t.stops_off.filter(s => num(s.lat) !== null && num(s.lon) !== null) : []);
    const offWhere = (s) => (s.off_line === true ? 'երթուղուց դուրս' : s.off_line === false ? 'պլանային երթուղու վրա' : null);
    const offText = (s) => ['Կանգառ ոչ խանութում՝ ' + s.minutes + ' րոպե',
        hm(s.from) + '–' + (s.to ? hm(s.to) : 'հիմա'), offWhere(s), s.lunch ? 'ճաշ' : null,
        s.long ? 'ահազանգ (երկար կանգառ)' : null].filter(Boolean).join(' · ');

    function offIcon(s) {
        const cls = 'lv-spin' + (s.lunch && !s.long ? ' is-lunch' : s.long ? ' is-long' : '') + (s.off_line ? ' is-off' : '')
            + (s.ongoing ? ' is-now' : '');
        const el = h('span', { class: 'lv-spin-box' }, h('span', { class: cls },
            icon(s.lunch && !s.long ? 'fa-utensils' : 'fa-square-parking'), h('b', { text: s.minutes + ' ր' })));
        return L.divIcon({ html: el.outerHTML, className: '', iconSize: [64, 26], iconAnchor: [32, 13] });
    }

    function drawOffStops(t, layer) {
        for (const s of offStops(t)) {
            L.marker([s.lat, s.lon], { icon: offIcon(s), keyboard: false, zIndexOffset: 800, riseOnHover: true })
                .bindTooltip(tip(offText(s)), { direction: 'top', offset: [0, -12] }).addTo(layer);
        }
    }

    // карточка: таблица стоянок не по плану, строка — кнопка к точке на карте
    function renderOffStops(t) {
        const list = offStops(t);
        $('lvOffBox').hidden = !Array.isArray(t.stops_off);
        $('lvOffNote').textContent = list.length
            ? list.length + ' կանգառ · ընդամենը ' + dur(list.reduce((m, s) => m + (num(s.minutes) || 0), 0))
                + (list.some(s => s.off_line) ? ' · երթուղուց դուրս՝ ' + list.filter(s => s.off_line).length : '')
                + ' · 5 րոպեից երկար, պահեստից և այս օրվա խանութներից դուրս'
            : 'Խանութներից և պահեստից դուրս 5 րոպեից երկար կանգառ չի եղել';
        const cell = (text, cls) => h('td', { class: cls || null, text });
        $('lvOff').replaceChildren(...list.map((s, i) => {
            const go = h('button', { type: 'button', class: 'lv-linkbtn lv-off-go', title: 'Ցույց տալ քարտեզում',
                'aria-label': 'Կանգառ ' + (i + 1) + '՝ ցույց տալ քարտեզում' }, icon('fa-location-dot'));
            go.addEventListener('click', () => showPoint(s.lat, s.lon, offText(s)));
            const note = [s.ongoing ? 'հիմա այնտեղ է' : null, s.lunch ? 'ճաշ' : null, s.long ? 'ահազանգ' : null].filter(Boolean).join(' · ');
            return h('tr', null,
                cell(String(i + 1), 'is-num'), cell(hm(s.from) + '–' + (s.to ? hm(s.to) : 'հիմա'), 'lv-nowrap'),
                cell(String(s.minutes), 'is-num' + (s.long ? ' is-bad' : '')),
                h('td', null, h('span', { class: s.off_line ? 'is-bad' : s.off_line === false ? null : 'is-mute', text: offWhere(s) || '—' }),
                    note ? h('small', { text: note }) : null),
                h('td', null, go));
        }));
    }

    // плановая линия (что водитель получил): широкий полупрозрачный коридор по рейсам — под фактическим путём
    function drawPlan(t, layer) {
        const r = t.route;
        if (!state.showPlan || !r || !Array.isArray(r.lines)) return;
        r.lines.forEach((line, i) => {
            if (line.length < 2) return;
            L.polyline(line, { color: LN.plan, weight: 12, opacity: 0.3, interactive: false, className: 'lv-l-plan' }).addTo(layer);
            hoverable(line, 'is-plan', planHover(t, i), layer);
        });
    }

    function renderTruckLayer(t) {
        if (!state.layer || state.replay.on) return;   // воспроизведение рисует своё — опрос его не стирает
        state.layer.clearLayers();   // подсказка точки — на карте, не в слое: ниже обновится по новым данным или уйдёт
        state.hover.fns = [];
        if (!t) { setArrows(null); forgetHover(); return; }
        // участки с перепробегом — широким бледно-красным ореолом под всем (весь участок, где потеряны км)
        for (const x of (t.detour && t.detour.items) || []) {
            if (!x.over || !Array.isArray(x.line) || x.line.length < 2) continue;
            L.polyline(x.line, { color: LN.over, weight: 22, opacity: 0.16, className: 'lv-l-over' })
                .bindTooltip(tip('Ավելորդ վազք՝ ' + legExcess(x) + ' կմ · ' + legName(x))).addTo(state.layer);
        }
        drawPlan(t, state.layer);
        if (t.track && t.track.length > 1) cased(t.track, LN.track, 'lv-l-track', state.layer, { interactive: false });
        // отклонения от плановой линии — участок пути красным (км и время — и в подсказке точки пути); «փոքր շեղում» —
        // оранжевым, объяснённое — серым; ширина та же, что у пути
        for (const r of (t.deviation && t.deviation.runs) || []) {
            if (!Array.isArray(r.line) || r.line.length < 2) continue;
            const [color, cls] = r.explained ? [LN.explained, 'lv-l-explained'] : r.minor ? [LN.minor, 'lv-l-minor'] : [LN.dev, 'lv-l-dev'];
            cased(r.line, color, cls, state.layer)
                .bindTooltip(tip((r.minor ? 'Փոքր շեղում՝ ' : 'Շեղում երթուղուց՝ ') + fmt(r.km, 1) + ' կմ · ' + hm(r.from)
                    + (r.to ? '–' + hm(r.to) : ' — հիմա') + (r.explained ? ' · բացատրված՝ ' + (REASON[r.explained.reason] || '') : '')));
        }
        setArrows(t.track);
        drawStart(t, state.layer);
        if (canReplay(t)) hoverable(t.track, 'is-track', trackHover(t), state.layer);   // поверх пути и отклонений
        if (state.data.depot) {   // склад — поверх линии подсказки (своя подсказка «Պահեստ»)
            L.circleMarker(state.data.depot, { radius: 8, color: '#ffffff', weight: 3, fillColor: '#0b57d0', fillOpacity: 1 })
                .bindTooltip(tip('Պահեստ')).addTo(state.layer);
        }
        const hv = state.hover;
        if (hv.at && hv.car === t.car_code) hoverAt(hv.at); else if (hv.at) forgetHover();
        // магазины плана, которых нет у терминала, — пустой номер (только вместе с плановой линией)
        const have = new Set((t.stops || []).map(s => s.customer_id));
        for (const p of (state.showPlan && t.route && t.route.points) || []) {
            if (have.has(p.customer_id)) continue;
            L.marker([p.lat, p.lon], { icon: pinIcon(p.no, null, false), keyboard: false })
                .bindTooltip(tip('№' + p.no + ' ըստ պլանի · տերմինալում չկա')).addTo(state.layer);
        }
        // магазин с несколькими накладными — прогноз у каждой его ожидающей точки (строка прогноза — по клиенту)
        const lateKey = (x) => x.customer_id ?? 's:' + x.stop_id;   // точка без клиента — своя
        const lateOf = new Map((t.late || []).filter(x => x.stop_id).map(x => [lateKey(x), x]));
        for (const s of t.stops || []) {
            if (num(s.lat) === null || num(s.lon) === null) continue;
            const [label, color] = storeOf(s);
            const lt = s.status === 'pending' ? lateOf.get(lateKey(s)) : null;   // №87: прогноз «не успеет» — красная обводка и строка в подсказке
            const text = (s.name || s.stop_id) + ' — ' + label + (s.planned_eta ? ' · պլան՝ ' + hm(s.planned_eta) : '')
                + (s.gps ? ' · GPS՝ ' + gpsText(s.gps) : '') + (lt ? ' · ' + lateText(lt) + ' (≈ ' + hm(lt.eta) + ')' : '');
            // магазин плана — с номером по плану; вне плана — кружок
            (s.plan_no ? L.marker([s.lat, s.lon], { icon: pinIcon(s.plan_no, color, !!lt), keyboard: false, zIndexOffset: 500 })
                : L.circleMarker([s.lat, s.lon], { radius: 7, color: lt ? '#ff6b79' : '#0e1116', weight: lt ? 3 : 2, fillColor: color, fillOpacity: 1 }))
                .bindTooltip(tip((s.plan_no ? '№' + s.plan_no + ' · ' : '') + text)).addTo(state.layer);
        }
        drawOffStops(t, state.layer);
        for (const a of t.alerts_log || []) {
            if (num(a.lat) === null || num(a.lon) === null) continue;
            if (a.kind === 'stop' && Array.isArray(t.stops_off)) continue;   // долгая стоянка — значком стоянки (drawOffStops)
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
        else if (a.kind === 'deviation') {
            more = ', ' + fmt(a.km, 1) + ' կմ' + (num(a.excess_km) !== null && a.excess_km > 0 ? ' · ավելորդ վազք +' + fmt(a.excess_km, 1) + ' կմ' : '');
            if (a.minor) return ['Փոքր շեղում' + more + explainedText(a), when];
        } else if (a.kind === 'sequence') {
            more = '՝ ' + (a.skipped || []).map(storeName).join(', ') + (a.jump ? ' (նախքան դրանք՝ ' + storeName(a.jump) + ')' : '');
        }
        if (a.explained) return [title + more + explainedText(a), when];
        // прогноз «не успеет»: что и насколько; время — прогноз прибытия (возврата), не начало события
        if (a.kind === 'late') return [lateText(a) + (a.name ? ' — ' + a.name : ''), '≈ ' + hm(a.eta) + ' (մինչև ' + hm(a.limit) + ')'];
        return [title + more, when];
    }

    const explainedText = (a) => (a.explained ? ' · բացատրված՝ ' + (REASON[a.explained.reason] || a.explained.reason)
        + (a.explained.note ? ' («' + a.explained.note + '»)' : '') : '');
    const legEnd = (e) => (e.kind === 'depot' ? 'Պահեստ' : storeName(e) + (e.stops > 1 ? ' (+' + (e.stops - 1) + ')' : ''));
    const legName = (x) => legEnd(x.from) + ' → ' + legEnd(x.to) + (x.ongoing ? ' (ընթացքում)' : '');
    // перепробег участка: идущий — прогноз (проехано + остаток до магазина), «≈»; в итог дня он не входит
    const legExcess = (x) => (x.ongoing ? '≈ +' + fmt(x.projected_excess_km, 1) : '+' + fmt(x.excess_km, 1));

    function renderCard() {
        const t = state.detail || (state.data && state.data.trucks.find(x => x.car_code === state.selected));
        $('lvCard').hidden = !t;
        if (!t) return;
        const [baseLabel, cls] = STATE[t.state] || STATE.standing;
        const label = stateLabel(t, baseLabel);
        $('lvPlate').textContent = t.car_code;
        $('lvSub').textContent = [t.name, t.planned ? null : 'պլանում չէ'].filter(Boolean).join(' · ');
        $('lvState').replaceChildren(h('span', { class: 'lv-dot ' + cls }), label);

        const active = (t.alerts_log || []).filter(a => a.active);
        // «не успеет» по магазинам — одной строкой (машина вышла поздно — опаздывает ко всем: 12 красных строк закрывают
        // экран телефона); список — по кнопке; возврат на склад — своей строкой
        const lateStores = active.filter(a => a.kind === 'late' && a.late_kind !== 'return');
        const grouped = lateStores.length > 1;
        const rowOf = (a) => h('div', null, icon((ALERT[a.kind] || ['fa-bell'])[0]), h('span', { text: alertText(a)[0] }));
        const rows0 = active.filter(a => !(grouped && lateStores.includes(a))).map(rowOf);
        if (grouped) {
            const over = lateStores.map(a => a.over_min), hi = Math.max(...over), lo = Math.min(...over);
            const btn = h('button', { type: 'button', class: 'lv-active-more', 'aria-expanded': String(state.lateOpen),
                'aria-controls': 'lvLateList', text: state.lateOpen ? 'Թաքցնել' : 'Ցույց տալ' });
            const list = h('ul', { id: 'lvLateList', class: 'lv-late-list' },
                ...lateStores.map(a => h('li', { text: alertText(a)[0] })));
            list.hidden = !state.lateOpen;
            btn.addEventListener('click', () => {
                state.lateOpen = !state.lateOpen;
                btn.setAttribute('aria-expanded', String(state.lateOpen));
                btn.textContent = state.lateOpen ? 'Թաքցնել' : 'Ցույց տալ';
                list.hidden = !state.lateOpen;
            });
            rows0.unshift(h('div', { class: 'lv-active-late' }, icon(ALERT.late[0]),
                h('span', { text: lateStores.length + ' խանութ ուշանում է՝ ' + (hi - lo <= 10 ? '≈ ' : 'մինչև ') + hi + ' րոպե' }),
                btn, list));
        }
        const refocus = !!document.activeElement && document.activeElement.classList.contains('lv-active-more');
        $('lvActive').hidden = !active.length;
        $('lvActive').replaceChildren(...rows0);
        if (refocus) { const b = $('lvActive').querySelector('.lv-active-more'); if (b) b.focus({ preventScroll: true }); }

        const p = t.position;
        const off = noForecast(t), until = off ? untilText(t) : null;   // прогноза нет: данные — на момент последних данных
        const rows = [];
        rows.push(field(off ? 'Վերջին հայտնի դիրքը' : 'Դիրքը', p ? [ago(p.age_s)] : '—', p ? 'GPS՝ ' + hms(p.at) + ' · ' + p.lat.toFixed(5) + ', ' + p.lon.toFixed(5) : 'GPS կետ դեռ չկա', p ? ageClass(p.age_s) : 'is-mute'));
        rows.push(field('Արագություն', p && p.speed_kmh !== null ? p.speed_kmh + ' կմ/ժ' : '—',
            p && p.heading !== null ? 'ուղղություն՝ ' + p.heading + '°' : null));
        const crew = [t.driver ? 'Վարորդ՝ ' + t.driver : null, t.helper ? 'Առաքիչ՝ ' + t.helper : null].filter(Boolean).join(' · ');
        const term = (t.drivers || [])[0];
        rows.push(field('Վարորդ / առաքիչ', crew || '—', term && term !== t.driver ? 'Տերմինալում՝ ' + term : null, null, true));
        const sg = t.stores;
        rows.push(field('Խանութներ', sg.done + ' / ' + sg.total + (sg.gps_visited ? ' · GPS-ով՝ ' + sg.gps_visited : ''),
            [sg.in_progress ? 'ընթացքի մեջ՝ ' + sg.in_progress : null,
                sg.unmarked ? 'GPS-ով այցելած, չնշված՝ ' + sg.unmarked : null].filter(Boolean).join(' · ') || null));
        rows.push(field('Այսօր, կմ (GPS)', fmt(t.km, 1), until));
        rows.push(field('Վառելիք', t.fuel_l === null ? '—' : ['≈ ' + fmt(t.fuel_l, 1) + ' լ', h('span', { class: 'lv-est', text: 'հաշվարկ' })],
            [t.fuel_l === null ? 'մեքենայի ծախսը նշված չէ կարգավորումներում' : null, fuelCheckText(t.fuel_check), until].filter(Boolean).join(' · ')));
        const ld = t.load;
        // до выезда в машине ещё ничего — план рейса, а не «0 կգ»
        const before = !ld.trips_gone && num(ld.planned_kg) !== null;
        rows.push(field('Բեռի մնացորդ', before ? 'պլանով՝ ' + fmt(ld.planned_kg) + ' կգ' : fmt(ld.remaining_kg) + ' կգ',
            [ld.trips_gone ? 'բեռնված այս երթում՝ ' + fmt(ld.trip_kg) + ' կգ, առաքված՝ ' + fmt(ld.delivered_kg) + ' կգ'
                + (ld.trips > 1 ? ' · երթ ' + ld.trips_gone + '/' + ld.trips : '') : 'մեքենան դեռ չի մեկնել պահեստից',
            ld.trips_gone && num(ld.planned_kg) !== null ? 'հաջորդ երթը՝ պլանով ' + fmt(ld.planned_kg) + ' կգ' : null,
            ld.refused_kg ? 'չառաքված՝ ' + fmt(ld.refused_kg) + ' կգ (մեքենայում՝ մինչև պահեստ)' : null,
            ld.returns_kg ? 'վերադարձ՝ ' + fmt(ld.returns_kg) + ' կգ (մեքենայում)' : null,
            ld.returns_unweighed ? ld.returns_unweighed + ' վերադարձ առանց քաշի' : null,
            ld.unweighed_lines ? ld.unweighed_lines + ' տող առանց քաշի' : null, until].filter(Boolean).join(' · ')));
        const nx = t.next;
        if (nx) {
            const d = delayText(nx.delay_min);
            // связи нет — прогноза нет: «как будто машина ещё там» вводит в заблуждение (live.car_view, forecast)
            const why = noContact(t) ? 'կապ չկա ' + hm(t.data_until) + '-ից'
                : (t.device && (t.device.gps === 'off' || t.device.gps === 'no_permission') ? 'GPS-ն անջատված է'
                    : (!t.position ? 'GPS դիրք դեռ չկա' : 'կապ չկա ' + hm(t.data_until) + '-ից'));
            const when = nx.eta_unknown ? 'Ժամանումը անհայտ է՝ ' + why
                : (nx.here ? 'տեղում է' : 'ժամանում ≈ ' + hm(nx.eta) + srcText(nx.eta_source));
            rows.push(field('Հաջորդ խանութը', nx.name || nx.stop_id,
                when + (nx.planned_eta ? ' · պլան՝ ' + hm(nx.planned_eta) : '') + (d ? ' · ' + d[0] : ''),
                d && d[1] !== 'is-ok' ? d[1] : null, true));
        }
        const back = (t.late || []).find(x => x.late_kind === 'return');
        rows.push(field('Վերադարձ պահեստ', t.return_eta ? '≈ ' + hm(t.return_eta) : '—',
            [t.return_eta ? srcText(t.return_source).trim().slice(1, -1) : null,
                back ? 'բոլոր երթերից հետո ≈ ' + hm(back.eta) + '՝ ' + lateText(back).toLowerCase() : null].filter(Boolean).join(' · ') || null,
            back ? 'is-bad' : null));
        rows.push(field('Վերջին կապը', t.last_contact ? hms(t.last_contact) : '—', t.contact_age_s !== null ? ago(t.contact_age_s) : null,
            ageClass(t.contact_age_s)));
        const dv = t.device;
        rows.push(field('Տերմինալ', dv ? [(dv.battery !== null ? dv.battery + '%' : '—'), dv.charging ? ' ⚡' : '']
            : '—', dv ? ['GPS՝ ' + (GPS[dv.gps] || '—'), 'ինտերնետ՝ ' + (NET[dv.net] || '—'), 'APK ' + (dv.app || '—'), 'տվյալ՝ ' + hm(dv.at)].join(' · ')
            : 'տերմինալը չի ուղարկում իր վիճակը (հին տարբերակ)', dv && dv.gps && dv.gps !== 'on' ? 'is-bad' : null, true));
        $('lvGrid').replaceChildren(...rows);
        renderStats(t);

        $('lvStopsBox').hidden = !t.stops;
        const lateKey = (x) => x.customer_id ?? 's:' + x.stop_id;   // магазин с прогнозом; точка без клиента — своя
        const lateOf = new Map((t.late || []).filter(x => x.stop_id).map(x => [lateKey(x), x]));
        // план — факт: плановое ETA, прибытие по GPS (нет — прогноз ≈), разница в минутах (> +15 — красным), стоянка
        $('lvStops').replaceChildren(...(t.stops || []).map((s, i) => {
            const [lab, color] = storeOf(s);
            const dot = h('span', { class: 'lv-dot' });
            dot.style.background = color;
            const lt = s.status === 'pending' ? lateOf.get(lateKey(s)) : null;
            const g = s.gps;
            const diff = g && s.planned_eta ? Math.round((epoch(g.arrive) - epoch(s.planned_eta)) / 60) : null;
            const cell = (text, cls) => h('td', { class: cls || null, text });
            const here = (g && g.here) || (t.next && t.next.here && t.next.stop_id === s.stop_id);
            const status = lt ? lateText(lt) : (here && !DONE.includes(s.status) ? 'տեղում է' : lab);
            return h('tr', { class: [lt ? 'is-late' : null, s.unmarked ? 'is-unmarked' : null].filter(Boolean).join(' ') || null },
                cell(String(num(s.plan_no) ?? num(s.seq) ?? i + 1), 'is-num'),   // номер — как на карте (место в плане)
                h('td', { class: 'lv-stop-name' }, h('span', { text: s.name || s.stop_id }), g ? h('small', { text: gpsText(g) }) : null),
                cell(s.planned_eta ? hm(s.planned_eta) : '—', 'is-num'),
                cell(g ? hm(g.arrive) : (s.eta ? '≈ ' + hm(s.eta) : '—'), 'is-num' + (g ? '' : ' is-mute')),
                cell(diff === null ? '—' : (diff > 0 ? '+' : diff < 0 ? '−' : '') + Math.abs(diff), 'is-num' + (diff > 15 ? ' is-bad' : '')),
                cell(g ? String(g.minutes) : '—', 'is-num'),
                h('td', { class: 'lv-stop-status' }, dot, h('span', { text: status })));
        }));
        renderReplayControls(t);
        const log = t.alerts_log || [];
        $('lvLogBox').hidden = !t.stops;
        $('lvLog').replaceChildren(...(log.length ? log.map(a => {
            const [text, when] = alertText(a);
            const cls = [a.active ? 'is-active' : null, a.minor ? 'is-minor' : null, a.explained ? 'is-explained' : null].filter(Boolean).join(' ');
            let act = null;
            if (canExplain() && EXPLAINS.includes(a.kind)) act = a.explained ? undoButton(a) : explainButton(t.car_code, a, text);
            return h('li', { class: cls || null }, icon((ALERT[a.kind] || ['fa-bell'])[0]), h('span', { text }), h('span', { class: 'when', text: when }), act);
        }) : [h('li', null, h('span'), h('span', { text: 'Ահազանգ չկա' }), h('span'))]));
        renderDetour(t);
        renderOffStops(t);
    }

    // ---------- показатели дня, плановая линия, отклонение (08.10) ----------
    const dur = (m) => (num(m) === null ? '—' : m < 60 ? m + ' ր' : Math.floor(m / 60) + ' ժ ' + (m % 60) + ' ր');
    const NO_DATA = 'տվյալ չկա';

    // почему плановой линии нет: машина не в плане, план не отправлен водителям, точки магазинов неизвестны
    const noRouteText = (t) => (!t.planned ? 'մեքենան այս օրվա պլանում չէ'
        : !t.plan_sent ? 'պլանը վարորդներին ուղարկված չէ' : 'պլանի խանութների կոորդինատները հայտնի չեն');

    // км: факт (GPS) против плана (сборка «Развоза» / длина линии по дорогам)
    function kmText(t) {
        const plan = t.route ? num(t.route.km) : null;
        if (!t.position) return [NO_DATA, plan !== null ? 'պլանով՝ ' + fmt(plan, 1) + ' կմ' : null, 'is-mute'];
        if (plan === null || plan <= 0) return [fmt(t.km, 1) + ' կմ', 'պլանի կմ-ը հայտնի չէ', null];
        const pct = Math.round((t.km / plan - 1) * 100);
        return [fmt(t.km, 1) + ' / ' + fmt(plan, 1) + ' կմ', (pct > 0 ? '+' : '') + pct + '% պլանից' + (t.closed ? '' : ' (օրը դեռ չի ավարտվել)'),
            t.closed && pct > 15 ? 'is-warn' : null];
    }

    function deviationText(t) {
        const d = t.deviation;
        if (!t.route) return ['—', noRouteText(t), 'is-mute'];
        if (!d) return ['չի հաշվվում', 'ճանապարհներով պլանային երթուղին դեռ պատրաստ չէ (ուղիղ գծով՝ ոչ)', 'is-mute'];
        if (!t.position) return [NO_DATA, 'GPS կետեր չկան', 'is-mute'];
        const parts = [d.minor ? 'փոքր՝ ' + d.minor : null, d.explained ? 'բացատրված՝ ' + d.explained : null].filter(Boolean);
        const sub = 'պլանային երթուղուց ավելի հեռու, քան ' + fmt(d.threshold_m) + ' մ' + (parts.length ? ' · ' + parts.join(', ') : '')
            + (d.active ? ' · հիմա երթուղուց դուրս է' : '');
        return d.count ? [d.count + ' անգամ · ' + fmt(d.km, 1) + ' կմ', sub, d.active ? 'is-bad' : d.alerts ? 'is-warn' : 'is-mute']
            : ['չկա', sub, 'is-ok'];
    }

    // следование плану: доля км езды в коридоре плановой линии (объяснённые отклонения не считаются)
    function adherenceText(t) {
        const d = t.deviation, p = d ? num(d.adherence_pct) : null;
        if (!d) return ['—', t.route ? 'ճանապարհներով պլանային երթուղին դեռ պատրաստ չէ' : noRouteText(t), 'is-mute'];
        if (p === null) return [NO_DATA, 'երթուղու վրա դեռ քիչ կմ կա', 'is-mute'];
        return [fmt(p, 1) + '%', fmt(d.counted_km, 1) + ' կմ-ից երթուղուց դուրս՝ ' + fmt(d.off_km, 1) + ' կմ (բացատրվածները՝ ոչ)',
            p >= 95 ? 'is-ok' : p >= 80 ? 'is-warn' : 'is-bad'];
    }
    // перепробег: сумма по участкам фактического порядка с перепробегом не меньше порога настроек
    function detourText(t) {
        const d = t.detour;
        if (!d) return ['—', t.planned ? 'պլանը հայտնի չէ' : 'մեքենան այս օրվա պլանում չէ', 'is-mute'];
        if (!d.legs) return [NO_DATA, 'պլանի հատվածներ GPS-ով դեռ չկան', 'is-mute'];
        const rule = 'հատվածի ավելորդ կմ-ը՝ ' + fmt(d.threshold_km, 1) + ' կմ-ից';
        if (!d.legs_over) return ['չկա', fmt(d.legs) + ' հատված · ' + rule, 'is-ok'];
        return ['+' + fmt(d.excess_km, 1) + ' կմ · +' + dur(d.excess_min),
            [amd(d.cost_amd) ? amd(d.cost_amd) + (d.fuel_price_estimated ? ' (վառելիքի գինը՝ լռելյայն)' : '') : 'արժեքը՝ անհայտ (մեքենայի ծախսը նշված չէ)',
                d.legs_over + ' հատված ' + d.legs + '-ից', d.approx ? 'պլանի կմ-ը՝ մոտավոր' : null].filter(Boolean).join(' · '),
            d.excess_km >= 5 ? 'is-bad' : 'is-warn'];
    }
    // порядок объезда: пропущенные сейчас магазины рейса и пары «обслужен раньше, хотя в плане позже»
    function sequenceText(t) {
        const q = t.sequence;
        if (!q) return ['—', 'պլանը հայտնի չէ', 'is-mute'];
        if (q.skipped.length) {
            return ['Բաց թողնված՝ ' + q.skipped.length, q.skipped.map(storeName).join(', ')
                + (q.pairs.length ? ' · պլանից շեղված՝ ' + q.pairs.length + ' անգամ' : ''), 'is-bad'];
        }
        if (q.pairs.length) {
            return ['Պլանից շեղված՝ ' + q.pairs.length + ' անգամ',
                q.pairs.map(x => storeName(x.first) + '-ը՝ ' + storeName(x.then) + '-ից առաջ').join(' · '), 'is-warn'];
        }
        return ['Ըստ պլանի', 'խանութները սպասարկվում են պլանի հերթականությամբ', 'is-ok'];
    }

    // «Ավելորդ վազք»: участки с перепробегом — откуда → куда, км факт / план, лишние км и минуты, ≈ ֏
    function renderDetour(t) {
        const d = t.detour, items = (d && d.items) || [];
        $('lvDetourBox').hidden = !items.length;
        if (!items.length) return;
        const over = items.filter(x => x.over);
        $('lvDetourNote').textContent = items.length + ' հատված (պահեստ → խանութներ → պահեստ՝ փաստացի հերթականությամբ) · '
            + 'ավելորդ է, եթե GPS-ով կմ-ը պլանից ավելի է առնվազն ' + fmt(d.threshold_km, 1) + ' կմ-ով'
            + (items.some(x => x.ongoing && x.over) ? ' · ընթացիկ հատվածը՝ կանխատեսում (ընդհանուր գումարում չէ)' : '');
        const cell = (text, cls) => h('td', { class: cls || null, text });
        $('lvDetour').replaceChildren(...(over.length ? over.map(x => h('tr', null,
            h('td', { class: 'lv-stop-name' }, h('span', { text: legName(x) }),
                h('small', { text: hm(x.from.at) + '–' + (x.to.at ? hm(x.to.at) : 'հիմա') + (x.consecutive ? '' : ' · պլանում հաջորդը չէ') })),
            cell(legExcess(x), 'is-num is-bad'),
            cell(fmt(x.km, 1) + ' / ' + (x.approx ? '≈ ' : '') + fmt(x.plan_km, 1), 'is-num'),   // план по прямой — «≈»
            cell(num(x.excess_min) > 0 ? '+' + fmt(x.excess_min) : fmt(x.excess_min), 'is-num'),
            cell(num(x.cost_amd) === null ? '—' : fmt(x.cost_amd), 'is-num')))
            : [h('tr', null, h('td', { colspan: '5', class: 'is-mute', text: 'Ավելորդ վազքով հատված չկա' }))]));
    }

    // ---------- «Բացատրել» (администратор): причина и заметка; сервер находит тревогу по виду и времени ----------
    let explaining = null;
    function explainButton(car, a, text) {
        const b = h('button', { type: 'button', class: 'lv-explain', 'aria-haspopup': 'dialog' }, icon('fa-comment-dots'), h('span', { text: 'Բացատրել' }));
        b.addEventListener('click', (e) => { e.stopPropagation(); openExplain(car, a, text, b); });
        return b;
    }
    function undoButton(a) {
        const b = h('button', { type: 'button', class: 'lv-explain is-undo' }, icon('fa-rotate-left'), h('span', { text: 'Չեղարկել' }));
        b.addEventListener('click', async () => {
            b.disabled = true;
            try { await api('/api/routes/live/unexplain', { id: a.explained.id }); showError(''); refresh(); }
            catch (e) { b.disabled = false; showError(e.message); }
        });
        return b;
    }
    function openExplain(car, a, text, opener) {
        explaining = { car, kind: a.kind, from: a.from, opener };
        $('lvExplainLead').textContent = text;
        $('lvExplainOpts').querySelectorAll('input[type="radio"]').forEach(r => { r.checked = false; });
        $('lvExplainNote').value = '';
        $('lvExplainErr').textContent = '';
        $('lvExplainSave').disabled = true;
        $('lvExplainDlg').showModal();
        $('lvExplainOpts').querySelector('input[type="radio"]').focus();
    }
    $('lvExplainOpts').addEventListener('change', () => { $('lvExplainSave').disabled = !$('lvExplainOpts').querySelector('input:checked'); });
    $('lvExplainClose').addEventListener('click', () => $('lvExplainDlg').close());
    $('lvExplainDlg').addEventListener('close', () => {
        const o = explaining && explaining.opener;
        explaining = null;
        if (o && o.isConnected) o.focus({ preventScroll: true });
    });
    $('lvExplainSave').addEventListener('click', async () => {
        const x = explaining, pick = $('lvExplainOpts').querySelector('input:checked');
        if (!x || !pick) return;
        $('lvExplainSave').disabled = true;
        try {
            await api('/api/routes/live/explain', { date: state.date || state.data.date, car: x.car, kind: x.kind, from: x.from,
                reason: pick.value, note: $('lvExplainNote').value.trim() });
            $('lvExplainDlg').close();
            refresh();
        } catch (e) {
            $('lvExplainErr').textContent = e.message;
            $('lvExplainSave').disabled = false;
        }
    });

    // точка максимальной скорости на карте: отдельный слой (опрос слой машины перерисовывает)
    function showPoint(lat, lon, text) {
        if (!state.map) return;
        if (state.pin) state.pin.remove();
        state.pin = L.circleMarker([lat, lon], { radius: 9, color: '#ffb547', weight: 3, fillColor: '#0e1116', fillOpacity: 0.9 })
            .bindTooltip(tip(text), { permanent: true, direction: 'top', offset: [0, -8] }).addTo(state.map);
        state.map.setView([lat, lon], Math.max(state.map.getZoom(), 15), { animate: !reduced() });
        if (window.matchMedia('(max-width: 899px)').matches) $('lvMap').scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' });
    }
    function clearPoint() { if (state.pin) { state.pin.remove(); state.pin = null; } }

    function renderStats(t) {
        const st = t.stats || {}, th = state.data ? state.data.thresholds : {};
        const r = t.route;
        const rows = [];
        rows.push(field('Պլանային երթուղի', r ? r.trips + ' երթ · ' + r.stops + ' խանութ' : '—',
            r ? (r.road ? 'ճանապարհներով, ինչպես «Առաքում» էջում'
                + (r.straight ? ' · ' + r.straight + ' հատված՝ ուղիղ գծով (ճանապարհ չգտնվեց, այնտեղ շեղումը չի հաշվվում)' : '')
                : 'ուղիղ գծով՝ ճանապարհների քարտեզը դեռ պատրաստ չէ') : noRouteText(t),
            r ? null : 'is-mute', true));
        const [km, kmSub, kmCls] = kmText(t);
        rows.push(field('Կմ՝ փաստ / պլան', km, kmSub, kmCls));
        const [dv, dvSub, dvCls] = deviationText(t);
        rows.push(field('Շեղում երթուղուց', dv, dvSub, dvCls));
        rows.push(field('Երթուղուն հետևում', ...adherenceText(t)));
        rows.push(field('Ավելորդ վազք', ...detourText(t)));
        rows.push(field('Հերթականություն', ...sequenceText(t), true));
        const ms = st.max_speed;
        let top = NO_DATA;
        if (ms) {
            top = h('button', { type: 'button', class: 'lv-linkbtn', title: 'Ցույց տալ քարտեզին' }, icon('fa-location-dot'), ms.kmh + ' կմ/ժ');
            top.addEventListener('click', () => showPoint(ms.lat, ms.lon, 'Առավելագույն արագություն՝ ' + ms.kmh + ' կմ/ժ · ' + hm(ms.at)));
        }
        rows.push(field('Առավելագույն արագություն', top, ms ? 'ժամը ' + hm(ms.at) + ' · սեղմեք՝ քարտեզին' : null,
            ms ? (th && ms.kmh > th.speed_kmh ? 'is-bad' : null) : 'is-mute'));
        rows.push(field('Միջին արագություն ընթացքում', num(st.avg_kmh) !== null ? st.avg_kmh + ' կմ/ժ' : NO_DATA, null,
            num(st.avg_kmh) !== null ? null : 'is-mute'));
        rows.push(field('Ընթացքում / կանգնած', num(st.moving_min) !== null ? dur(st.moving_min) + ' / ' + dur(st.stopped_min) : NO_DATA,
            st.nodata_min ? 'առանց տվյալի՝ ' + dur(st.nodata_min) : null, num(st.moving_min) !== null ? null : 'is-mute'));
        const off = offStops(t);
        if (Array.isArray(t.stops_off)) {
            rows.push(field('Կանգառներ ոչ խանութում', off.length ? off.length + ' · ' + dur(off.reduce((m, s) => m + (num(s.minutes) || 0), 0)) : 'չկա',
                off.length ? [off.some(s => s.off_line) ? 'երթուղուց դուրս՝ ' + off.filter(s => s.off_line).length : null,
                    off.some(s => s.long) ? 'երկար (ահազանգ)՝ ' + off.filter(s => s.long).length : null].filter(Boolean).join(' · ') || null : null,
                off.some(s => s.long || s.off_line) ? 'is-warn' : 'is-ok'));
        }
        const ov = st.overspeed;
        rows.push(field('Արագության գերազանցում', ov ? (ov.count ? ov.count + ' անգամ · ' + dur(ov.minutes) : 'չկա') : NO_DATA,
            th ? 'ավելի, քան ' + fmt(th.speed_kmh) + ' կմ/ժ՝ ' + fmt(th.speed_sec) + ' վ-ից երկար' : null,
            ov ? (ov.count ? 'is-warn' : 'is-ok') : 'is-mute'));
        $('lvStats').replaceChildren(...rows);
        // «план — факт» магазинов: та же сводка км и отклонений одной строкой
        $('lvPf').textContent = [r && num(r.km) !== null ? 'Կմ՝ պլան ' + fmt(r.km, 1) + (t.position ? ' · փաստ ' + fmt(t.km, 1) : '') : null,
            t.deviation && t.position ? 'շեղում՝ ' + (t.deviation.count ? t.deviation.count + ' անգամ, ' + fmt(t.deviation.km, 1) + ' կմ' : 'չկա') : null]
            .filter(Boolean).join(' · ');
        $('lvPf').hidden = !$('lvPf').textContent;
    }

    // ---------- воспроизведение дня ----------
    // ползунок — по t.track_t (секунды эпохи, 1:1 с t.track); на время воспроизведения — снимок машины в своей отрисовке:
    // опрос карточку обновляет, а слой машины не трогает (renderTruckLayer)
    const canReplay = (t) => !!(t && Array.isArray(t.track_t) && Array.isArray(t.track) && t.track.length > 1
        && t.track_t.length === t.track.length);

    function renderReplayControls(t) {
        $('lvReplay').hidden = !canReplay(t);
        if (state.replay.on || !canReplay(t)) return;
        const r = $('lvReplayRange'), last = t.track_t[t.track_t.length - 1];
        r.min = String(Math.floor(t.track_t[0] / 60) * 60);   // шаг — минута: начало на целой минуте
        r.max = String(last);
        r.value = String(last);
        $('lvReplayTime').textContent = clockOf(last);
    }

    // положение на треке в момент x — между соседними точками по времени; и индекс последней точки не позже x
    function replayAt(x) {
        const tt = state.replay.truck.track_t, tr = state.replay.truck.track;
        let lo = 0, hi = tt.length - 1;
        if (x <= tt[lo]) return [tr[lo], lo];
        if (x >= tt[hi]) return [tr[hi], hi];
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (tt[mid] <= x) lo = mid; else hi = mid; }
        const f = (x - tt[lo]) / ((tt[hi] - tt[lo]) || 1);
        return [[tr[lo][0] + (tr[hi][0] - tr[lo][0]) * f, tr[lo][1] + (tr[hi][1] - tr[lo][1]) * f], lo];
    }

    // магазин в момент x: отмечен до x — его итог; машина у него по GPS — «տեղում»; уехала, не отметив, — «չնշված»; иначе — ждёт
    function storeAt(s, x) {
        const done = epoch(s.delivered_at);
        if (DONE.includes(s.status) && done !== null && done <= x) return STORE[s.status];
        const g = s.gps;
        if (g) {
            const a = epoch(g.arrive), l = g.leave ? epoch(g.leave) : Infinity;
            if (a <= x && x <= l) return STORE.here;
            if (s.unmarked && x > l) return STORE.unmarked;
        }
        return STORE.pending;
    }

    function startReplay() {
        const rp = state.replay, t = state.detail;
        if (rp.on) return true;
        if (!canReplay(t) || !state.layer) return false;
        Object.assign(rp, { on: true, truck: t });
        forgetHover();
        state.hover.fns = [];   // при воспроизведении — подсказка только плановой линии
        state.layer.clearLayers();
        if (state.data.depot) {
            L.circleMarker(state.data.depot, { radius: 8, color: '#ffffff', weight: 3, fillColor: '#0b57d0', fillOpacity: 1 })
                .bindTooltip(tip('Պահեստ')).addTo(state.layer);
        }
        drawPlan(t, state.layer);   // плановая линия — и при воспроизведении (сравнить путь с планом)
        drawOffStops(t, state.layer);   // стоянки не по плану — и при воспроизведении
        L.polyline(t.track, { color: LN.track, weight: LINE_W, opacity: 0.3, interactive: false }).addTo(state.layer);   // весь день — тускло
        rp.prefix = cased([], LN.track, 'lv-l-track', state.layer, { interactive: false });   // пройденное — с обводкой
        setArrows(t.track);
        rp.stops = (t.stops || []).filter(s => num(s.lat) !== null && num(s.lon) !== null).map(s => [s,
            L.circleMarker([s.lat, s.lon], { radius: 7, color: '#0e1116', weight: 2, fillColor: STORE.pending[1], fillOpacity: 1 })
                .bindTooltip(tip(s.name || s.stop_id)).addTo(state.layer)]);
        rp.ghost = L.circleMarker(t.track[0], { radius: 9, color: '#0e1116', weight: 3, fillColor: '#e8edf4', fillOpacity: 1, interactive: false })
            .addTo(state.layer);
        // один раз показать весь день; без анимации, если пользователь просил меньше движения
        state.map.fitBounds(L.latLngBounds(t.track).pad(0.15), { maxZoom: 15, animate: !reduced() });
        $('lvReplayExit').hidden = false;
        return true;
    }

    function setReplay(x) {
        const rp = state.replay, tt = rp.truck.track_t;
        rp.t = Math.min(Math.max(x, tt[0]), tt[tt.length - 1]);
        const [p, i] = replayAt(rp.t);
        rp.prefix.setLatLngs([...rp.truck.track.slice(0, i + 1), p]);
        rp.prefix.casing.setLatLngs(rp.prefix.getLatLngs());
        rp.ghost.setLatLng(p);
        for (const [s, m] of rp.stops) m.setStyle({ fillColor: storeAt(s, rp.t)[1] });
        $('lvReplayRange').value = String(Math.round(rp.t));
        $('lvReplayTime').textContent = clockOf(rp.t);
    }

    function tick(now) {
        const rp = state.replay;
        if (!rp.playing) return;
        const dt = rp.last ? Math.min(1, (now - rp.last) / 1000) : 0;   // вкладка была скрыта — без скачка
        rp.last = now;
        setReplay(rp.t + dt * (Number($('lvReplaySpeed').value) || 60));
        if (rp.t >= rp.truck.track_t[rp.truck.track_t.length - 1]) { setPlaying(false); return; }
        rp.raf = requestAnimationFrame(tick);
    }

    function setPlaying(on) {
        const rp = state.replay;
        rp.playing = on;
        cancelAnimationFrame(rp.raf);
        rp.last = 0;
        $('lvReplayPlay').replaceChildren(icon(on ? 'fa-pause' : 'fa-play'), h('span', { text: on ? 'Դադար' : 'Նվագարկել' }));
        if (on) rp.raf = requestAnimationFrame(tick);
    }

    function stopReplay() {
        const rp = state.replay;
        if (!rp.on) return;
        setPlaying(false);
        Object.assign(rp, { on: false, truck: null, prefix: null, ghost: null, stops: [] });
        $('lvReplayExit').hidden = true;
        renderTruckLayer(state.detail);
        renderReplayControls(state.detail);
    }

    $('lvReplayPlay').addEventListener('click', () => {
        const rp = state.replay;
        if (rp.playing) { setPlaying(false); return; }
        const fresh = !rp.on;
        if (!startReplay()) return;
        const tt = rp.truck.track_t;
        if (fresh || rp.t >= tt[tt.length - 1]) setReplay(tt[0]);   // с начала дня; дошло до конца — заново
        setPlaying(true);
    });
    $('lvReplayRange').addEventListener('input', () => {
        if (startReplay()) setReplay(Number($('lvReplayRange').value));
    });
    $('lvReplayExit').addEventListener('click', stopReplay);
    // «Պլանային երթուղի» на карте: вкл/выкл (воспроизведение — со следующего запуска)
    $('lvPlanToggle').checked = state.showPlan;
    $('lvPlanToggle').addEventListener('change', () => {
        state.showPlan = $('lvPlanToggle').checked;
        savePlanToggle(state.showPlan);
        renderTruckLayer(state.detail);
    });

    // ---------- данные ----------
    const query = () => (state.date ? '?date=' + encodeURIComponent(state.date) : '');

    async function refresh() {
        if (state.busy) { state.again = true; return; }   // дата/выбор сменились во время запроса — один повтор после него
        state.busy = true;
        try {
            const asked = state.date;   // дату сменили во время запроса — ответ устарел: не показывать, повторить
            const data = await api('/api/routes/live' + query());
            if (state.date !== asked) { state.again = true; return; }
            state.data = data;
            if (!state.date) $('lvDate').value = data.date;
            $('lvToday').hidden = !state.date;
            if (state.selected && !data.trucks.some(t => t.car_code === state.selected)) {
                state.selected = null;
                state.detail = null;
                stopReplay();
            }
            if (state.selected) {
                const sep = query() ? '&' : '?';
                const one = await api('/api/routes/live/truck' + query() + sep + 'car=' + encodeURIComponent(state.selected));
                if (state.date !== asked) { state.again = true; return; }
                state.detail = one.truck;
                retrack(one.truck);
            }
            syncAlarms(data);
            renderProblems();
            renderSummary(data.trucks);
            renderList(data.trucks);
            renderMarkers(data.trucks);
            renderTruckLayer(state.detail);
            renderCard();
            renderBanner();
            showError('');
            $('lvLive').hidden = false;
            $('lvLive').classList.toggle('is-paused', !!state.date);
            $('lvLiveText').textContent = 'Թարմացվել է ' + hms(data.now);
        } catch (e) {
            showError(e.message);
        } finally {
            state.busy = false;
            if (state.again) { state.again = false; refresh(); }
        }
    }

    // линия трека ещё привязывается к дорогам (track_pending): карточку — ещё раз через 3 с, не больше двух раз на машину
    // и день (прошлый день не опрашивается — без этого там осталась бы линия без привязки); воспроизведение не трогаем
    function retrack(t) {
        const key = state.date + '|' + (t ? t.car_code : '');
        const r = state.retrack && state.retrack.key === key ? state.retrack : (state.retrack = { key, tries: 0, timer: 0 });
        clearTimeout(r.timer);
        if (!t || !t.track_pending || r.tries >= 2) return;
        r.tries += 1;
        r.timer = setTimeout(() => {
            if (state.retrack === r && state.selected === t.car_code && !state.replay.on) refresh();
        }, 3000);
    }

    // карточка выбранной машины на телефоне — под картой и списком: прокрутить к ней
    function focusCard() {
        if (window.matchMedia('(max-width: 899px)').matches) $('lvCard').scrollIntoView({ block: 'start', behavior: reduced() ? 'auto' : 'smooth' });
    }

    function select(car) {
        stopReplay();
        clearPoint();
        state.lateOpen = false;
        state.selected = state.selected === car ? null : car;
        state.detail = null;
        state.retrack = null;   // новый выбор — снова до двух дозапросов линии
        renderCard();
        renderTruckLayer(null);
        if (state.data) { renderList(state.data.trucks); renderMarkers(state.data.trucks); }
        refresh().then(() => {
            const t = state.detail;
            if (t && t.position && state.map) state.map.panTo([t.position.lat, t.position.lon], { animate: !reduced() });
            if (t) focusCard();
        });
    }

    // опрос — только пока вкладка видна (телефон в кармане не тратит трафик); прошлый день не обновляется. Звук включён —
    // и в скрытой вкладке, раз в 60 с: счётчик «(N) ⚠» во вкладке и звук новой красной тревоги работают из другой вкладки
    function schedule() {
        clearInterval(state.timer);
        state.timer = null;
        if (state.date) return;
        if (document.visibilityState === 'visible') state.timer = setInterval(refresh, POLL_MS);
        else if (state.alarm.sound) state.timer = setInterval(refresh, HIDDEN_POLL_MS);
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
        stopReplay();
        clearPoint();
        state.lateOpen = false;
        state.selected = null;
        state.detail = null;
        refresh();
        schedule();
    });
    $('lvToday').addEventListener('click', () => { $('lvDate').value = ''; $('lvDate').dispatchEvent(new Event('change')); });
    // «Տեսա բոլորը» — все новые; «Ձայն» — включение (жест пользователя будит AudioContext: пробный сигнал)
    $('lvAlarmAck').addEventListener('click', () => {
        ack(state.alarm.probs.filter(isNew).map(p => p.key));
        const first = $('lvProbList').querySelector('.lv-prob');   // баннер скрылся — фокус на список проблем
        if (first) first.focus({ preventScroll: true });
        $('lvProbList').scrollTop = 0;
    });
    $('lvSound').addEventListener('click', () => {
        state.alarm.sound = !state.alarm.sound;
        saveSound(state.alarm.sound);
        renderSound();
        if (state.alarm.sound) wakeAudio().then(() => { renderSound(); beep(); });
        schedule();
    });
    renderSound();
    armAudio();

    initMap();
    refresh();
    schedule();
})();

/* «Маршруты» — стеклянные панели поверх карты сворачиваются (пользователь 12.10 «всё это сильно закрывает карту»).
   Общий модуль для «Առաքում» (/routes/dispatch) и «Մեքենաները առցանց» (/routes/live), стили — routes_ui.css (.rt-pmin,
   .rt-ppill, .rt-pdock, .rt-mapbtn).

   RtPanels.init({ page, stage, panels, map, enabled, onChange }):
     page     — ключ запоминания (localStorage «rtPanels:<page>»: свёрнутые панели и «Միայն քարտեզ»);
     stage    — «сцена» с картой: в ней доки таблеток (.rt-pdock--top/left/right/bottom), на ней классы
                rt-panels-on (механизм включён) и rt-min-<id> (панель свёрнута) — страница двигает по ним кнопки карты;
     panels   — [{ id, title, edge: top|left|right|bottom, els: [элементы панели], anchor: куда поставить «—» (по
                умолчанию els[0]), dock: свой контейнер таблетки (иначе док края), label: () => текст таблетки после
                названия (счётчик, главные числа), alert: () => пульсировать ли свёрнутой таблетке }];
     map      — Leaflet-карта: рядом с +/− кнопка «Միայն քարտեզ» (все панели — в таблетки; повторно — как было);
     enabled  — () => bool: механизм работает (ПК); на телефоне вкладки страницы, панели не сворачиваются;
     onChange — (reason) => void: панель свернули/развернули — пересчитать раскладку и вписать выбранное.
   RtPanels.refresh() — после перерисовки страницы: подписи таблеток, их видимость (панель скрыта страницей — таблетки
   нет), пульс. Клавиша M (фокус не в поле ввода, нет открытого окна) — «Միայն քարտեզ». Свернули — фокус на таблетку,
   развернули — на «—». Анимация ≤ 150 мс, при prefers-reduced-motion — без неё. */
(function () {
    'use strict';
    const CHEV = { top: 'fa-chevron-down', left: 'fa-chevron-right', right: 'fa-chevron-left', bottom: 'fa-chevron-up' };
    let cfg = null;
    const st = { min: new Set(), mapOnly: false, before: [] };
    const key = () => 'rtPanels:' + cfg.page;
    const on = () => !!(cfg && (!cfg.enabled || cfg.enabled()));

    function load() {
        try {
            const v = JSON.parse(window.localStorage.getItem(key()) || 'null');
            if (v && Array.isArray(v.min)) {
                const ids = new Set(cfg.panels.map(p => p.id));
                st.min = new Set(v.min.filter(id => ids.has(id)));
                st.mapOnly = !!v.mapOnly;
                st.before = Array.isArray(v.before) ? v.before.filter(id => ids.has(id)) : [];
            }
        } catch (e) { /* хранилище недоступно — всё развёрнуто */ }
    }
    function save() {
        try {
            window.localStorage.setItem(key(), JSON.stringify({ min: [...st.min], mapOnly: st.mapOnly, before: st.before }));
        } catch (e) { /* только на эту страницу */ }
    }
    const shown = (p) => p.els.some(el => el && !el.hidden);   // панель есть на странице (страница её не прячет)

    function button(cls, attrs) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = cls;
        Object.entries(attrs || {}).forEach(([k, v]) => b.setAttribute(k, v));
        return b;
    }
    function dockOf(p) {
        if (p.dock) return p.dock;
        let d = cfg.stage.querySelector(':scope > .rt-pdock--' + p.edge);
        if (!d) {
            d = document.createElement('div');
            d.className = 'rt-pdock rt-pdock--' + p.edge;
            cfg.stage.appendChild(d);
        }
        return d;
    }
    function build(p) {
        const ctl = p.els[0];
        if (ctl && !ctl.id) ctl.id = 'rtPanel-' + p.id;
        p.min = button('rt-pmin', { 'aria-label': 'Ծալել «' + p.title + '»', title: 'Ծալել', 'aria-expanded': 'true', 'aria-controls': ctl.id });
        p.min.dataset.panel = p.id;
        p.min.innerHTML = '<i class="fas fa-minus" aria-hidden="true"></i>';
        p.min.addEventListener('click', () => collapse(p.id, true));
        (p.anchor || ctl).appendChild(p.min);
        p.pill = button('rt-ppill rt-ppill--' + p.edge, { 'aria-expanded': 'false', 'aria-controls': ctl.id });
        p.pill.dataset.panel = p.id;
        p.pill.innerHTML = '<span class="rt-ppill__t"></span><span class="rt-ppill__n"></span><i class="fas ' + CHEV[p.edge] + '" aria-hidden="true"></i>';
        p.pill.querySelector('.rt-ppill__t').textContent = p.title;
        p.pill.hidden = true;
        p.pill.addEventListener('click', () => expand(p.id, true));
        dockOf(p).appendChild(p.pill);
        // страница прячет и показывает панель сама (нет выбранной машины, нет проблем) — таблетка следом
        if (typeof window.MutationObserver !== 'undefined') {
            const mo = new MutationObserver(() => paint(p));
            p.els.forEach(el => el && mo.observe(el, { attributes: true, attributeFilter: ['hidden'] }));
        }
    }
    function paint(p) {
        const active = on(), min = active && st.min.has(p.id), has = shown(p);
        p.els.forEach(el => el && el.classList.toggle('rt-is-min', min));
        p.min.hidden = !active;
        p.min.setAttribute('aria-expanded', String(!min));
        p.pill.hidden = !(min && has);
        if (!p.pill.hidden) {
            const n = p.label ? String(p.label() || '') : '';
            p.pill.querySelector('.rt-ppill__n').textContent = n;
            p.pill.querySelector('.rt-ppill__n').hidden = !n;
            p.pill.setAttribute('aria-label', 'Բացել «' + p.title + '»' + (n ? ' · ' + n : ''));
        }
        p.pill.classList.toggle('is-alert', !!(min && has && p.alert && p.alert()));
        cfg.stage.classList.toggle('rt-min-' + p.id, min && has);
    }
    function paintAll() {
        cfg.stage.classList.toggle('rt-panels-on', on());
        cfg.panels.forEach(paint);
        if (cfg.mapBtn) {
            cfg.mapBtn.setAttribute('aria-pressed', String(st.mapOnly));
            cfg.mapBtn.querySelector('i').className = 'fas ' + (st.mapOnly ? 'fa-compress' : 'fa-expand');
            cfg.mapBtn.closest('.leaflet-control').hidden = !on();
        }
    }
    // короткое появление развёрнутой панели (без него — при reduced motion, стили)
    function appear(p) {
        p.els.forEach(el => {
            if (!el) return;
            el.classList.remove('rt-is-opening');
            void el.offsetWidth;
            el.classList.add('rt-is-opening');
            setTimeout(() => el.classList.remove('rt-is-opening'), 200);
        });
    }
    function changed(reason) {
        save();
        paintAll();
        if (cfg.onChange) cfg.onChange(reason);
    }
    const byId = (id) => cfg.panels.find(p => p.id === id);
    function collapse(id, focus) {
        const p = byId(id);
        if (!p || st.min.has(id)) return;
        st.min.add(id);
        changed('collapse');
        if (focus && !p.pill.hidden) p.pill.focus();
    }
    function expand(id, focus) {
        const p = byId(id);
        if (!p || !st.min.has(id)) return;
        st.min.delete(id);
        if (st.mapOnly) { st.mapOnly = false; st.before = []; }   // развернули одну — «Միայն քարտեզ» снято
        changed('expand');
        appear(p);
        if (focus) p.min.focus();
    }
    function toggleMapOnly() {
        if (!on()) return;
        if (!st.mapOnly) {
            st.before = [...st.min];
            cfg.panels.forEach(p => st.min.add(p.id));
            st.mapOnly = true;
        } else {
            const back = new Set(st.before);
            cfg.panels.filter(p => st.min.has(p.id) && !back.has(p.id)).forEach(appear);
            st.min = back;
            st.mapOnly = false;
            st.before = [];
        }
        changed('map-only');
    }
    function mapButton(map) {
        if (typeof window.L === 'undefined' || !map) return;
        const Ctl = window.L.Control.extend({
            options: { position: 'topleft' },
            onAdd() {
                const box = window.L.DomUtil.create('div', 'leaflet-bar leaflet-control rt-mapctl');
                const b = button('rt-mapbtn', { 'aria-pressed': 'false', 'aria-label': 'Միայն քարտեզ', title: 'Միայն քարտեզ (M)' });
                b.innerHTML = '<i class="fas fa-expand" aria-hidden="true"></i>';
                window.L.DomEvent.disableClickPropagation(box);
                b.addEventListener('click', toggleMapOnly);
                box.appendChild(b);
                cfg.mapBtn = b;
                return box;
            },
        });
        new Ctl().addTo(map);
    }
    function typing(t) {
        return !!(t && t.closest && t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'));
    }

    window.RtPanels = {
        init(options) {
            cfg = options;
            load();
            cfg.panels.forEach(build);
            mapButton(cfg.map);
            document.addEventListener('keydown', (e) => {
                if (e.code !== 'KeyM' || e.ctrlKey || e.metaKey || e.altKey || e.repeat || typing(e.target)) return;
                if (document.querySelector('dialog[open]') || !on()) return;
                e.preventDefault();
                toggleMapOnly();
            });
            paintAll();
        },
        refresh() { if (cfg) paintAll(); },
        isMin: (id) => !!(cfg && on() && st.min.has(id) && shown(byId(id))),
        collapse, expand, toggleMapOnly,
    };
})();

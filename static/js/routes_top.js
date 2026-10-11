// Верхняя панель раздела «Маршруты» (templates/_routes_top.html, Tesla+). Выпадашки — кнопки-раскрывашки
// (aria-expanded / aria-controls): открыта одна; Esc — закрыть и фокус на кнопку; клик мимо или уход фокуса — закрыть.
// Тема ☀/☾: html[data-rt-theme] и Bootstrap data-bs-theme, выбор — в этом браузере (rtTheme); до отрисовки их ставит
// _routes_theme.html. Смена — событие rt-theme-change на document: карты перекрашивают слои и подложку.
(function () {
    'use strict';
    const top = document.getElementById('rtTop');
    if (!top) return;
    const root = document.documentElement;

    // ---------- тема ----------
    const themeBtns = Array.from(top.querySelectorAll('[data-rt-theme-set]'));
    const theme = () => (root.dataset.rtTheme === 'light' ? 'light' : 'dark');
    const syncTheme = () => themeBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.rtThemeSet === theme())));
    themeBtns.forEach((b) => b.addEventListener('click', () => {
        const t = b.dataset.rtThemeSet === 'light' ? 'light' : 'dark';
        if (t === theme()) return;
        root.dataset.rtTheme = t;
        root.setAttribute('data-bs-theme', t);
        try { window.localStorage.setItem('rtTheme', t); } catch (e) { /* хранилище недоступно — тема до перехода */ }
        syncTheme();
        document.dispatchEvent(new CustomEvent('rt-theme-change', { detail: { theme: t } }));
    }));
    syncTheme();

    // ---------- выпадашки ----------
    const toggles = Array.from(top.querySelectorAll('[data-rt-pop]'));
    const panelOf = (b) => document.getElementById(b.getAttribute('aria-controls'));
    let open = null;   // кнопка открытой выпадашки

    function close(refocus) {
        if (!open) return;
        const b = open;
        open = null;
        b.setAttribute('aria-expanded', 'false');
        panelOf(b).hidden = true;
        top.classList.remove('is-open');
        if (refocus) b.focus();
    }
    function show(b) {
        close(false);
        open = b;
        b.setAttribute('aria-expanded', 'true');
        panelOf(b).hidden = false;
        top.classList.add('is-open');
    }
    toggles.forEach((b) => b.addEventListener('click', () => { if (open === b) close(false); else show(b); }));
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && open) { e.preventDefault(); close(true); }
    });
    // клик мимо открытой выпадашки и её кнопки — закрыть
    document.addEventListener('pointerdown', (e) => {
        if (open && !open.contains(e.target) && !panelOf(open).contains(e.target)) close(false);
    });
    // фокус ушёл из кнопки и её выпадашки (Tab дальше по странице) — закрыть
    top.addEventListener('focusout', (e) => {
        if (!open || !e.relatedTarget) return;
        if (!open.contains(e.relatedTarget) && !panelOf(open).contains(e.relatedTarget)) close(false);
    });
    // лист на узком экране: окно стало широким — закрыть (кнопки листа уже не видно)
    const WIDE = window.matchMedia('(min-width: 900px)');
    const onWidth = () => { if (WIDE.matches && open && open.id === 'rtTopMenu') close(false); };
    if (WIDE.addEventListener) WIDE.addEventListener('change', onWidth); else WIDE.addListener(onWidth);
})();

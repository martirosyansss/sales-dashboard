// Меню раздела «Маршруты» слева (templates/_routes_side.html): свернуть до иконок — помнится в этом браузере;
// уже 992 px — выезжающая панель (Esc и клик по затемнению закрывают, фокус — в меню и обратно на кнопку).

// Тема ☀/☾ (Tesla+, routes.css): html[data-rt-theme] и Bootstrap data-bs-theme, выбор — в этом браузере (rtTheme);
// до отрисовки их ставит _routes_theme.html. Смена — событие rt-theme-change: карты перекрашивают слои и подложку.
(function () {
    'use strict';
    const buttons = Array.from(document.querySelectorAll('[data-rt-theme-set]'));
    if (!buttons.length) return;
    const root = document.documentElement;
    const current = () => (root.dataset.rtTheme === 'light' ? 'light' : 'dark');
    const sync = () => buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.rtThemeSet === current())));
    buttons.forEach((b) => b.addEventListener('click', () => {
        const theme = b.dataset.rtThemeSet === 'light' ? 'light' : 'dark';
        if (theme === current()) return;
        root.dataset.rtTheme = theme;
        root.setAttribute('data-bs-theme', theme);
        try { window.localStorage.setItem('rtTheme', theme); } catch (e) { /* хранилище недоступно — тема до перехода */ }
        sync();
        document.dispatchEvent(new CustomEvent('rt-theme-change', { detail: { theme } }));
    }));
    sync();
})();

(function () {
    'use strict';
    const shell = document.getElementById('rtShell');
    if (!shell) return;
    const side = document.getElementById('rtSecNav'), fold = document.getElementById('rtSecNavFold'),
        openBtn = document.getElementById('rtSecNavOpen'), closeBtn = document.getElementById('rtSecNavClose'),
        scrim = document.getElementById('rtSecNavScrim');
    const KEY = 'rtSecNavCollapsed';
    const NARROW = window.matchMedia('(max-width: 991.98px)');
    const links = Array.from(side.querySelectorAll('.rt-secnav-link'));

    // меню закреплено под шапкой дашборда: её высота (на узком экране она раскрывается — пересчитать)
    const nav = document.querySelector('.app-nav');
    const navH = () => shell.style.setProperty('--rt-secnav-nav',
        (nav && getComputedStyle(nav).position === 'sticky' ? Math.ceil(nav.getBoundingClientRect().height) : 0) + 'px');
    navH();
    if (nav && typeof window.ResizeObserver !== 'undefined') new ResizeObserver(navH).observe(nav); else window.addEventListener('resize', navH);

    // свёрнуто — подпись пункта во всплывающей подсказке (раскрыто — подпись видна, подсказка не нужна)
    function syncFold() {
        const on = shell.classList.contains('is-collapsed');
        fold.setAttribute('aria-expanded', String(!on));
        const label = on ? 'Բացել ընտրացանկը' : 'Փակել ընտրացանկը';
        fold.setAttribute('aria-label', label);
        fold.title = label;
        links.forEach((a) => { if (on) a.title = a.textContent.trim(); else a.removeAttribute('title'); });
    }
    // карты Leaflet (Развоз, «Մեքենաները առցանց», Ուսուցում…) слушают resize окна — ширина страницы поменялась, перерисовать
    const relayout = () => window.dispatchEvent(new Event('resize'));
    side.addEventListener('transitionend', (e) => { if (e.target === side && e.propertyName === 'width') relayout(); });
    fold.addEventListener('click', () => {
        const on = !shell.classList.contains('is-collapsed');
        shell.classList.toggle('is-collapsed', on);
        try { window.localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* хранилище недоступно — только до перехода */ }
        syncFold();
        if (!(parseFloat(getComputedStyle(side).transitionDuration) > 0)) relayout();   // без анимации transitionend не придёт
    });
    syncFold();

    // узкий экран: выезжающее меню
    const focusables = () => [closeBtn, ...links];
    const main = shell.querySelector('.rt-shell-main');
    function setDrawer(open, refocus) {
        shell.classList.toggle('is-open', open);
        main.inert = open;   // страница под затемнением — ни нажатий, ни фокуса (нижние вкладки «Развоза» и т. п.)
        openBtn.setAttribute('aria-expanded', String(open));
        scrim.hidden = !open;
        document.documentElement.classList.toggle('rt-secnav-lock', open);
        if (open) {
            side.setAttribute('role', 'dialog');
            side.setAttribute('aria-modal', 'true');
            side.setAttribute('aria-label', 'Երթուղիների բաժիններ');
            (side.querySelector('[aria-current="page"]') || closeBtn).focus();
        } else {
            ['role', 'aria-modal', 'aria-label'].forEach((a) => side.removeAttribute(a));
            if (refocus) openBtn.focus();
        }
    }
    const isOpen = () => shell.classList.contains('is-open');
    openBtn.addEventListener('click', () => setDrawer(true));
    closeBtn.addEventListener('click', () => setDrawer(false, true));
    scrim.addEventListener('click', () => setDrawer(false, true));
    document.addEventListener('keydown', (e) => {
        if (!isOpen()) return;
        if (e.key === 'Escape') { e.preventDefault(); setDrawer(false, true); return; }
        if (e.key !== 'Tab') return;
        // фокус не уходит из открытого меню на страницу под затемнением
        const f = focusables(), first = f[0], last = f[f.length - 1];
        if (!side.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    // окно стало широким — меню снова колонкой, панель закрыта
    const onWidth = () => { if (!NARROW.matches && isOpen()) setDrawer(false); };
    if (NARROW.addEventListener) NARROW.addEventListener('change', onWidth); else NARROW.addListener(onWidth);
})();

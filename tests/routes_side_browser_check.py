# -*- coding: utf-8 -*-
"""Проверка меню раздела «Маршруты» слева (владелец 08.10: полоса вкладок → меню слева) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_side_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_cost_browser_check.py (настоящие шаблоны, статика и blueprint, поддельная ERP, планы 01.10 и 30.09 — на
«Развозе» рабочий экран), роль — администратор; всё во временной папке. Порт 8791 на 127.0.0.1.

A 1920×1080, 1366×768 и 1366×650 (ноутбук владельца): на всех 11 страницах меню видно колонкой, текущий пункт — своя страница, кнопки «☰» нет,
  горизонтальной прокрутки страницы нет;
  всё меню без прокрутки внутри, кнопка свернуть (в шапке меню) и «Կարգավորումներ» на экране; на «Մեքենաները առցանց»
  при 1366 карта ≥ 600 px (три колонки — только когда ширины страницы хватает);
B свернуть (1366×650, «Ակնարկ»): меню 64 px, подписи — подсказками, после анимации — событие resize (карты Leaflet
  перерисовываются); переход на «Առաքում» — меню сразу свёрнуто (помнится), рабочий экран и карта по ширине окна,
  шапка рабочего экрана помещается; развернуть — снова 244 px; без хранилища (localStorage бросает) страница цела;
C «Առաքում» 1366 и 1920: рабочий экран виден, карта не уже 600 px и не выходит за окно, до низа окна;
D телефон 390×860, все 11 страниц: колонки нет, кнопка «☰ <страница>» с её названием, прокрутки вбок нет;
  кнопка открывает меню (видно, фокус на текущем пункте, страница не прокручивается), Tab не уходит из меню,
  Esc закрывает и фокус на кнопке; клик по затемнению закрывает;
Снимки — в output/routes-side/ (не в git).
Ошибки страницы (pageerror) — провал.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_cost_browser_check as cost  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
import routes_dispatch_browser_check as base  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8791
BASE = f'http://127.0.0.1:{PORT}'
OUT = ROOT / 'output' / 'routes-side'
PAGES = [('/routes', 'Ակնարկ'), ('/routes/optimize', 'Օպտիմալացում'), ('/routes/learning', 'Ուսուցում'),
         ('/routes/dispatch?date=2026-10-01', 'Առաքում'), ('/routes/live', 'Մեքենաները առցանց'),
         ('/routes/drivers', 'Վարորդներ'), ('/routes/araqich', 'Առաքիչների KPI'), ('/routes/garage', 'Ավտոտնակ'),
         ('/routes/pay', 'Աշխատավարձ'), ('/routes/cost', 'Առաքման արժեք'), ('/routes/settings', 'Կարգավորումներ')]
SHOTS = {'/routes': 'overview', '/routes/dispatch?date=2026-10-01': 'dispatch', '/routes/live': 'live'}


def build(tmp):
    app = cost.build(tmp)
    app.template_context_processors[None].append(lambda: {'is_admin': True, 'is_garage': False})
    return app


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    tmp = tempfile.mkdtemp(prefix='side-check-')
    app = build(tmp)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            def new_page(w, h, init=None):
                ctx = browser.new_context(viewport={'width': w, 'height': h})
                pg = ctx.new_page()
                if init:
                    pg.add_init_script(init)
                pg.on('pageerror', lambda e: errors.append(f'pageerror {w}: {e}'))
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    pg.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
                return pg

            def go(pg, url):
                pg.goto(BASE + url, wait_until='domcontentloaded')
                pg.wait_for_selector('#rtShell', timeout=30000)
                pg.wait_for_load_state('load')
                pg.wait_for_timeout(400)

            no_hscroll = lambda pg: pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')  # noqa: E731
            side_w = lambda pg: round(pg.eval_on_selector('#rtSecNav', 'e => e.getBoundingClientRect().width'))  # noqa: E731
            current = lambda pg: pg.get_attribute('#rtSecNav a[aria-current="page"]', 'href')  # noqa: E731
            # ноутбук владельца ~1366×650: пункты не прокручиваются внутри меню, кнопка свернуть и «Կարգավորումներ» на экране
            menu_fit = lambda pg: pg.evaluate('''() => {
                const sc = document.querySelector('.rt-secnav-scroll'), vis = (s) => { const r = document.querySelector(s).getBoundingClientRect();
                    return r.width > 0 && r.top >= 0 && r.bottom <= innerHeight; };
                return { fits: sc.scrollHeight <= sc.clientHeight, sh: sc.scrollHeight, ch: sc.clientHeight,
                         fold: vis('#rtSecNavFold'), foot: vis('.rt-secnav-foot a') }; }''')  # noqa: E731

            # A
            for w, h in ((1920, 1080), (1366, 768), (1366, 650)):
                pg = new_page(w, h)
                for url, _ in PAGES:
                    go(pg, url)
                    path = url.split('?')[0]
                    check(pg.is_visible('#rtSecNav') and not pg.is_visible('#rtSecNavOpen') and current(pg) == path
                          and side_w(pg) == 244 and no_hscroll(pg),
                          f'A {w}×{h} {path}: меню колонкой ({side_w(pg)} px), текущий {current(pg)}, без прокрутки вбок')
                    fit = menu_fit(pg)
                    check(fit['fits'] and fit['fold'] and fit['foot'],
                          f'A {w}×{h} {path}: всё меню без прокрутки внутри, «Փակել» и «Կարգավորումներ» видны {fit}')
                    if path == '/routes/live' and w < 1600:
                        mw = round(pg.eval_on_selector('#lvMap', 'e => e.getBoundingClientRect().width'))
                        check(mw >= 600, f'A {w}×{h} /routes/live: карта {mw} px (две колонки, не 320 px)')
                    if h == 650 and url in SHOTS:
                        pg.screenshot(path=str(OUT / f'{SHOTS[url]}-1366x650-expanded.png'))
                    if w == 1920:
                        pg.screenshot(path=str(OUT / f'all-1920-{path.strip("/").replace("/", "-")}.png'))
                pg.context.close()

            # B + C
            pg = new_page(1366, 650)
            go(pg, '/routes')
            pg.evaluate('window.__rs = 0; window.addEventListener("resize", () => { window.__rs++; })')
            pg.click('#rtSecNavFold')
            pg.wait_for_timeout(500)
            check(side_w(pg) == 64 and pg.get_attribute('#rtSecNavFold', 'aria-expanded') == 'false'
                  and pg.get_attribute('#rtSecNav a[href="/routes/cost"]', 'title') == 'Առաքման արժեք'
                  and pg.evaluate('window.__rs') >= 1 and no_hscroll(pg),
                  f'B свёрнуто: {side_w(pg)} px, подсказки, resize {pg.evaluate("window.__rs")}')
            check(pg.evaluate("localStorage.getItem('rtSecNavCollapsed')") == '1', 'B запомнено в браузере')
            fit = menu_fit(pg)
            check(fit['fits'] and fit['fold'] and fit['foot'], f'B свёрнуто: кнопка «Բացել» видна, всё без прокрутки {fit}')
            pg.screenshot(path=str(OUT / 'overview-1366x650-collapsed.png'))
            for url in ('/routes/live', '/routes/dispatch?date=2026-10-01'):
                go(pg, url)
                check(side_w(pg) == 64 and 'is-collapsed' in pg.get_attribute('#rtShell', 'class') and no_hscroll(pg),
                      f'B {url}: меню сразу свёрнуто после перехода ({side_w(pg)} px)')
                pg.screenshot(path=str(OUT / f'{SHOTS[url]}-1366x650-collapsed.png'))

            def ws_ok(tag):
                pg.wait_for_selector('#dpWs:not([hidden])', timeout=30000)
                pg.wait_for_timeout(600)
                ws = pg.eval_on_selector('#dpWs', 'e => { const r = e.getBoundingClientRect(); return [r.left, r.right, r.bottom]; }')
                mp = pg.eval_on_selector('#dpWs .leaflet-container', 'e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }')
                vw = pg.evaluate('document.documentElement.clientWidth')
                vh = pg.evaluate('window.innerHeight')
                fits = pg.eval_on_selector('#rtDispatch .dp-dayboard', 'e => e.scrollWidth <= e.clientWidth')
                check(ws[1] <= vw and mp[0] >= 600 and mp[1] >= 250 and ws[2] <= vh + 1 and fits and no_hscroll(pg),
                      f'C {tag}: рабочий экран {ws}, карта {mp}, окно {vw}×{vh}, шапка помещается {fits}')
                return mp[0]

            narrow_map = ws_ok('1366 свёрнуто')
            pg.evaluate('window.__rs = 0; window.addEventListener("resize", () => { window.__rs++; })')
            pg.click('#rtSecNavFold')
            pg.wait_for_timeout(600)
            check(side_w(pg) == 244 and pg.evaluate('window.__rs') >= 1
                  and pg.evaluate("localStorage.getItem('rtSecNavCollapsed')") == '0'
                  and pg.get_attribute('#rtSecNav a[href="/routes/cost"]', 'title') is None, 'B развёрнуто: 244 px, resize, запомнено')
            wide_map = ws_ok('1366 раскрыто')
            check(narrow_map - wide_map >= 150, f'C карта шире при свёрнутом меню ({narrow_map} > {wide_map})')
            pg.context.close()

            pg = new_page(1920, 1080)
            go(pg, '/routes/dispatch?date=2026-10-01')
            ws_ok('1920')
            pg.context.close()

            pg = new_page(1366, 768, "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });")
            go(pg, '/routes')
            pg.click('#rtSecNavFold')
            pg.wait_for_timeout(400)
            check(side_w(pg) == 64 and not [e for e in errors if 'blocked' in e], 'B без хранилища: меню сворачивается, ошибок нет')
            pg.context.close()

            # D
            pg = new_page(390, 860)
            for url, name in PAGES:
                go(pg, url)
                path = url.split('?')[0]
                check(not pg.is_visible('#rtSecNav') and pg.is_visible('#rtSecNavOpen')
                      and pg.inner_text('.rt-secnav-open-v') == name and no_hscroll(pg),
                      f'D 390 {path}: кнопка «☰ {pg.inner_text(".rt-secnav-open-v")}», меню скрыто, без прокрутки вбок')
                pg.click('#rtSecNavOpen')
                pg.wait_for_timeout(350)
                focus = pg.evaluate('document.activeElement.getAttribute("href")')
                check(pg.is_visible('#rtSecNav') and focus == path and pg.get_attribute('#rtSecNavOpen', 'aria-expanded') == 'true'
                      and pg.evaluate('getComputedStyle(document.documentElement).overflow') == 'hidden'
                      and pg.eval_on_selector('#rtSecNav', 'e => e.getBoundingClientRect().right') <= 390,
                      f'D {path}: меню открыто, фокус на {focus}')
                if url in SHOTS:
                    pg.screenshot(path=str(OUT / f'{SHOTS[url]}-390-drawer.png'))
                for _ in range(14):
                    pg.keyboard.press('Tab')
                check(pg.evaluate('document.getElementById("rtSecNav").contains(document.activeElement)'), f'D {path}: Tab в меню')
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(350)
                check(not pg.is_visible('#rtSecNav') and pg.evaluate('document.activeElement.id') == 'rtSecNavOpen'
                      and pg.evaluate('getComputedStyle(document.documentElement).overflow') != 'hidden',
                      f'D {path}: Esc закрыл, фокус на кнопке')
                if url in SHOTS:
                    pg.screenshot(path=str(OUT / f'{SHOTS[url]}-390-closed.png'))
                if path == '/routes':
                    pg.click('#rtSecNavOpen')
                    pg.wait_for_timeout(350)
                    pg.mouse.click(370, 400)   # затемнение справа от меню
                    pg.wait_for_timeout(350)
                    check(not pg.is_visible('#rtSecNav') and pg.is_hidden('#rtSecNavScrim'), 'D клик по затемнению закрыл')
            pg.context.close()
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERR  ' + e)
    ok = all(results) and not errors
    print(f'\n{sum(results)}/{len(results)} проверок, ошибок страницы: {len(errors)} — {"OK" if ok else "FAIL"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

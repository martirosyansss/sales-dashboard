# -*- coding: utf-8 -*-
"""Проверка верхней панели раздела «Маршруты» (Tesla+, решение владельца 11.10: вместо меню слева №92 и тёмной шапки
дашборда) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_top_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_cost_browser_check.py (настоящие шаблоны, статика и blueprint, поддельная ERP, планы 01.10 и 30.09 — на
«Развозе» рабочий экран); роль задаёт проверка (администратор, «Гараж», «Склад»); всё во временной папке. Порт 8791.

A администратор, 1366×768, обе темы: тёмной шапки дашборда и подвала нет; 4 группы; у текущей страницы своя группа
  подсвечена; группа открывается (aria-expanded, видна выпадашка, пункты с подсказками, текущий — aria-current), открыта
  только одна; Esc закрывает и фокус на кнопке группы; клик мимо закрывает; Tab с кнопки — в первый пункт выпадашки;
  ⋮⋮⋮ — разделы Sales Dashboard (Dashboard, /settings, /managers …); пользователь — имя и «Ելք» (POST /logout);
B тема ☀/☾ в панели: атрибуты html, aria-pressed, rtTheme в браузере, событие rt-theme-change;
C ширины 390 / 1366 / 1920 на 6 страницах: прокрутки вбок нет; уже 900 px — группы в кнопке «☰ <страница>», лист
  со всеми страницами (видно, Esc закрывает); шире — кнопки нет, группы видны;
D роли: «Гараж» — три плоские ссылки (журнал, карта, водители), без ⋮⋮⋮ и ⚙; «Склад» — одна ссылка, без листа;
Снимки — в output/routes-top/ (не в git) или в папку из ROUTES_TOP_SHOTS. Ошибки страницы и консоли — провал (кроме
сетевых для внешних ресурсов — шрифты, CDN, плитки).
"""
from __future__ import annotations

import logging
import os
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
OUT = Path(os.environ.get('ROUTES_TOP_SHOTS') or ROOT / 'output' / 'routes-top')
GROUPS = ['Պլանավորում', 'Առաքում', 'Թիմ', 'Ֆինանսներ']
PAGES = [('/routes', 'Պլանավորում'), ('/routes/garage', 'Թիմ'), ('/routes/live', 'Առաքում'),
         ('/routes/settings', None), ('/routes/pay', 'Ֆինանսներ'), ('/routes/dispatch?date=2026-10-01', 'Առաքում')]
SHOT_PAGES = {'/routes/garage': 'garage', '/routes/live': 'live', '/routes': 'overview', '/routes/settings': 'settings'}
ROLE = {'admin': {'is_admin': True, 'is_garage': False, 'is_public_role': False},
        'garage': {'is_admin': False, 'is_garage': True, 'is_public_role': True},
        'warehouse': {'is_admin': False, 'is_garage': False, 'is_public_role': True}}
# у поддельного приложения нет базы терминалов и ERP: карта машин отвечает 400 («Առաքիչ» не подключён), зарплата — 503;
# страницы показывают это своим сообщением — к панели раздела не относится
HARNESS_GAPS = ('/api/routes/live', '/api/routes/pay')
NO_MOTION = '*, *::before, *::after { transition: none !important; animation: none !important; }'


def harness_gap(msg) -> bool:
    url = (msg.location or {}).get('url', '')
    return 'Failed to load resource' in msg.text and any(url.split('?')[0].endswith(x) for x in HARNESS_GAPS)


def build(tmp, who):
    app = cost.build(tmp)
    app.template_context_processors[None].append(lambda: dict(ROLE[who['role']], current_username='Արամ'))
    return app


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    OUT.mkdir(parents=True, exist_ok=True)
    results, errors = [], []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    who = {'role': 'admin'}
    tmp = tempfile.mkdtemp(prefix='top-check-')
    app = build(tmp, who)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            def new_page(w, h, theme='dark'):
                ctx = browser.new_context(viewport={'width': w, 'height': h})
                if theme == 'light':
                    ctx.add_init_script("try { localStorage.setItem('rtTheme', 'light'); } catch (e) {}")
                pg = ctx.new_page()
                pg.on('pageerror', lambda e: errors.append(f'pageerror {w}: {e}'))
                pg.on('console', lambda m: errors.append(f'console {w}: {m.text} {(m.location or {}).get("url", "")}')
                      if m.type == 'error' and not base.is_ignorable(m) and not harness_gap(m) else None)
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    pg.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
                return pg

            def go(pg, url):
                pg.goto(BASE + url, wait_until='domcontentloaded')
                pg.wait_for_selector('#rtTop', timeout=30000)
                pg.wait_for_load_state('load')
                pg.wait_for_timeout(500)

            def shot(pg, name, full=True):
                pg.add_style_tag(content=NO_MOTION)
                pg.screenshot(path=str(OUT / f'{name}.png'), full_page=full)

            no_hscroll = lambda pg: pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')  # noqa: E731
            expanded = lambda pg, sel: pg.get_attribute(sel, 'aria-expanded')  # noqa: E731
            opened = lambda pg: pg.evaluate("[...document.querySelectorAll('#rtTop [data-rt-pop][aria-expanded=\"true\"]')].map(b => b.id)")  # noqa: E731

            # A + B: администратор, обе темы
            for theme in ('dark', 'light'):
                pg = new_page(1366, 768, theme)
                for url, grp in PAGES[:4]:
                    go(pg, url)
                    path = url.split('?')[0]
                    st = pg.evaluate("""() => ({ nav: !!document.querySelector('.app-nav'), foot: document.body.innerText.includes('READ-ONLY'),
                        pills: [...document.querySelectorAll('.rt-top-pill')].map(b => b.textContent.trim()),
                        cur: [...document.querySelectorAll('.rt-top-pill.is-current')].map(b => b.textContent.trim()),
                        theme: document.documentElement.dataset.rtTheme, bg: getComputedStyle(document.querySelector('.rt-top')).backgroundColor })""")
                    check(not st['nav'] and not st['foot'] and st['pills'] == GROUPS and st['cur'] == ([grp] if grp else [])
                          and st['theme'] == theme, f'A {theme} {path}: шапки дашборда и подвала нет, группы, текущая {st["cur"]}')
                    if path in SHOT_PAGES:
                        shot(pg, f'{SHOT_PAGES[path]}-{theme}')
                        btn = '#rtTopG%d' % (GROUPS.index(grp) + 1 if grp else 1)
                        pg.click(btn)
                        pg.wait_for_timeout(200)
                        shot(pg, f'{SHOT_PAGES[path]}-{theme}-open', full=False)
                        pg.keyboard.press('Escape')
                # выпадашки на «Ավտոտնակ»
                go(pg, '/routes/garage')
                for i in range(1, 5):
                    pg.click(f'#rtTopG{i}')
                    pg.wait_for_timeout(100)
                    check(opened(pg) == [f'rtTopG{i}'] and pg.is_visible(f'#rtTopG{i}Panel'),
                          f'A {theme}: группа {GROUPS[i - 1]} открыта одна ({opened(pg)})')
                cur = pg.eval_on_selector('#rtTopG3Panel', "p => [...p.querySelectorAll('[aria-current=\"page\"]')].map(a => a.getAttribute('href'))")
                hints = pg.eval_on_selector_all('#rtTopG3Panel .rt-top-hint', 'e => e.map(x => x.textContent.trim()).filter(Boolean).length')
                pg.click('#rtTopG4')
                pg.wait_for_timeout(100)
                check(cur == ['/routes/garage'] and hints == 3 and opened(pg) == [], f'A {theme}: в «Թիմ» текущий {cur}, подсказок {hints}, повторный клик закрыл')
                pg.click('#rtTopG2')
                pg.keyboard.press('Escape')
                focus = pg.evaluate('document.activeElement && document.activeElement.id')
                check(opened(pg) == [] and focus == 'rtTopG2' and not pg.is_visible('#rtTopG2Panel'), f'A {theme}: Esc закрыл, фокус на кнопке ({focus})')
                pg.click('#rtTopG1')
                pg.mouse.click(700, 600)
                pg.wait_for_timeout(100)
                check(opened(pg) == [], f'A {theme}: клик мимо закрыл')
                pg.focus('#rtTopG4')
                pg.keyboard.press('Enter')
                pg.keyboard.press('Tab')
                href = pg.evaluate('document.activeElement.getAttribute("href")')
                check(expanded(pg, '#rtTopG4') == 'true' and href == '/routes/pay', f'A {theme}: клавиатура — Enter открыл, Tab в первый пункт ({href})')
                pg.keyboard.press('Tab')
                pg.keyboard.press('Tab')   # ушли из выпадашки дальше по панели — закрыта
                pg.wait_for_timeout(100)
                check(expanded(pg, '#rtTopG4') == 'false', f'A {theme}: фокус ушёл из выпадашки — закрыта')
                pg.click('#rtTopApps')
                apps = pg.eval_on_selector_all('#rtTopAppsPanel a', 'e => e.map(a => a.getAttribute("href"))')
                check(pg.is_visible('#rtTopAppsPanel') and {'/', '/settings', '/managers', '/customers-grid', '/production', '/courier', '/reports'} <= set(apps),
                      f'A {theme}: ⋮⋮⋮ — {len(apps)} разделов Sales Dashboard')
                if theme == 'dark':
                    shot(pg, 'apps-dark-open', full=False)
                pg.click('#rtTopUser')
                user = pg.eval_on_selector('#rtTopUserPanel', 'p => ({ who: p.querySelector(".rt-top-who b").textContent, act: p.querySelector("form").getAttribute("action"), m: p.querySelector("form").method })')
                check(opened(pg) == ['rtTopUser'] and user == {'who': 'Արամ', 'act': '/logout', 'm': 'post'}, f'A {theme}: пользователь {user}')
                if theme == 'light':
                    shot(pg, 'user-light-open', full=False)
                pg.keyboard.press('Escape')
                # B тема
                other = 'light' if theme == 'dark' else 'dark'
                pg.evaluate("window.__ev = []; document.addEventListener('rt-theme-change', e => window.__ev.push(e.detail.theme))")
                pg.click(f'[data-rt-theme-set="{other}"]')
                b = pg.evaluate("""() => ({ t: document.documentElement.dataset.rtTheme, bs: document.documentElement.getAttribute('data-bs-theme'),
                    ls: localStorage.getItem('rtTheme'), ev: window.__ev,
                    p: document.querySelector('[data-rt-theme-set="%s"]').getAttribute('aria-pressed') })""" % other)
                check(b == {'t': other, 'bs': other, 'ls': other, 'ev': [other], 'p': 'true'}, f'B {theme} → {other}: {b}')
                pg.context.close()

            # C ширины
            for w, h in ((390, 860), (1366, 768), (1920, 1080)):
                pg = new_page(w, h, 'light' if w == 390 else 'dark')
                for url, _ in PAGES:
                    go(pg, url)
                    path = url.split('?')[0]
                    narrow = w < 900
                    check(no_hscroll(pg) and pg.is_visible('#rtTopMenu') == narrow and pg.is_visible('.rt-top-nav') == (not narrow),
                          f'C {w}×{h} {path}: без прокрутки вбок, {"кнопка «☰»" if narrow else "группы"}')
                    if narrow:
                        pg.click('#rtTopMenu')
                        n = pg.eval_on_selector_all('#rtTopSheet .rt-top-item', 'e => e.filter(a => a.getBoundingClientRect().width > 0).length')
                        check(pg.is_visible('#rtTopSheet') and n == 11 and no_hscroll(pg), f'C 390 {path}: лист открыт, страниц {n}')
                        if path in SHOT_PAGES:
                            shot(pg, f'{SHOT_PAGES[path]}-390-sheet', full=False)
                        pg.keyboard.press('Escape')
                        check(not pg.is_visible('#rtTopSheet'), f'C 390 {path}: Esc закрыл лист')
                    elif w == 1920 and path in SHOT_PAGES:
                        shot(pg, f'{SHOT_PAGES[path]}-1920-dark', full=False)
                pg.context.close()

            # D роли
            who['role'] = 'garage'
            pg = new_page(1366, 768)
            go(pg, '/routes/garage')
            d = pg.evaluate("""() => ({ links: [...document.querySelectorAll('#rtTop a')].map(a => a.getAttribute('href')),
                cur: document.querySelector('#rtTop .rt-top-nav [aria-current="page"]')?.getAttribute('href'),
                apps: !!document.getElementById('rtTopApps'), menu: !!document.getElementById('rtTopMenu') })""")
            check(set(d['links']) == {'/routes/garage', '/routes/live', '/routes/drivers'} and d['cur'] == '/routes/garage'
                  and not d['apps'] and d['menu'], f'D «Гараж»: {d}')
            shot(pg, 'garage-role-dark', full=False)
            pg.context.close()
            pg = new_page(390, 860, 'light')
            go(pg, '/routes/live')
            pg.click('#rtTopMenu')
            n = pg.eval_on_selector_all('#rtTopSheet .rt-top-item', 'e => e.length')
            check(n == 3 and no_hscroll(pg), f'D «Гараж» 390: лист из {n} страниц')
            pg.context.close()
            who['role'] = 'warehouse'
            pg = new_page(390, 860)
            go(pg, '/routes/warehouse')
            d = pg.evaluate("""() => ({ links: [...document.querySelectorAll('#rtTop a')].map(a => a.getAttribute('href')),
                vis: !!document.querySelector('.rt-top-nav').offsetWidth, menu: !!document.getElementById('rtTopMenu') })""")
            check(d == {'links': ['/routes/warehouse'], 'vis': True, 'menu': False} and no_hscroll(pg), f'D «Склад» 390: {d}')
            shot(pg, 'warehouse-role-390', full=False)
            pg.context.close()
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERROR', e)
    check(not errors, f'ошибок страницы и консоли: {len(errors)}')
    print(f'{sum(results)}/{len(results)} OK')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

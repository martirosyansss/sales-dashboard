# -*- coding: utf-8 -*-
"""Общая часть браузерных проверок табличных страниц «Маршрутов» (Tesla+, фаза 4): страница в обеих темах ☀/☾ на
390 / 1366 / 1920 — тема применена, у страницы нет горизонтальной прокрутки, ошибок страницы и консоли нет.
Имя без префикса test_: pytest его не собирает; вызывают проверки страниц (routes_*_browser_check.py)."""
from __future__ import annotations

import routes_dispatch_browser_check as base

WIDTHS = ((390, 860), (1366, 900), (1920, 1080))


def themes_and_widths(browser, url, ready, check, errors, tag):
    """url — полный адрес; ready(page) — дождаться данных страницы; check(cond, msg) и errors — из проверки страницы."""
    for theme in ('dark', 'light'):
        for w, h in WIDTHS:
            pg = browser.new_page(viewport={'width': w, 'height': h})
            pg.on('pageerror', lambda e: errors.append(f'pageerror {tag} {theme} {w}: {e}'))
            pg.on('console', lambda m: errors.append(f'console {tag} {theme} {w}: {m.text}')
                  if m.type == 'error' and not base.is_ignorable(m) else None)
            pg.add_init_script("try { localStorage.setItem('rtTheme', '%s'); } catch (e) {}" % theme)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                pg.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
            pg.goto(url)
            ready(pg)
            pg.wait_for_timeout(300)
            st = pg.evaluate("""() => ({ t: document.documentElement.dataset.rtTheme,
                wide: document.documentElement.scrollWidth > document.documentElement.clientWidth })""")
            check(st['t'] == theme and not st['wide'], f'{tag} {theme} {w}: тема применена, без горизонтальной прокрутки ({st})')
            pg.close()

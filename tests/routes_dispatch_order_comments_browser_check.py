# -*- coding: utf-8 -*-
"""Комментарий ERP к заказу на «Առաքում»: в строке магазина плана (рабочий экран и список) и в «Նախորդ օրերից».

Запуск из корня проекта:  python tests/routes_dispatch_order_comments_browser_check.py [папка для снимков]
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение — из tests/routes_dispatch_browser_check.py.
Порт 8792. Комментарий с HTML остаётся текстом; один и тот же у двух заказов магазина показан один раз.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder  # noqa: E402

PORT = 8792
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
HTML = 'տանել հինգշաբթի <img src=x onerror="window.__xss=1"> <b>300֏</b>'
OLD = 'Ֆակտուրա տալ <i>old</i>'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-comments-'), FakeClient())

    class _C:
        application = app
        post = staticmethod(lambda url, json=None: app.test_client().post(url, json=json))
    orders = [replace(_dorder(1, 101, 400.0), comment=HTML), replace(_dorder(3, 101, 50.0), comment=HTML),
              replace(_dorder(2, 102, 300.0, agent=2), comment='Երկրորդ'), _dorder(4, 999, 50.0),
              replace(_dorder(3, 104, 1200.0, agent=2), comment='A'), replace(_dorder(7, 104, 5.0, agent=2), comment='B'),
              replace(_dorder(6, 102, 70.0, day=date(2026, 9, 28)), comment=OLD)]
    with app.test_request_context():
        _dispatch_setup(_C, orders)
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)
    views._same_day_now = lambda: datetime(2026, 9, 30, 18, 0)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for layout in ('list', 'ws'):
                ctx = browser.new_context(viewport={'width': 1600, 'height': 950})
                if layout == 'list':
                    ctx.add_init_script("(() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();")
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    ctx.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
                page = ctx.new_page()
                page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
                page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
                page.on('dialog', lambda d: d.accept())
                page.goto(f'{BASE}/routes/dispatch?date={DAY}')
                page.wait_for_selector('#dpBody', state='visible', timeout=30000)
                page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
                page.evaluate("document.getElementById('dpPrepOpen')?.click()")
                page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
                page.evaluate("document.getElementById('dpBuild').click()")
                page.wait_for_selector('.dp-stop', timeout=30000)
                page.wait_for_timeout(800)
                if page.locator('#dpWizard[open]').count():
                    page.click('#dpGuideClose')
                    page.wait_for_timeout(300)
                notes = page.evaluate("() => [...document.querySelectorAll('.dp-stop-note')].map(n => n.textContent)")
                print('     ', layout, notes)
                check(HTML in notes and notes.count(HTML) >= 1, f'{layout}: комментарий в строке магазина как текст')
                check(all(n == HTML or n in ('Երկրորդ', 'A · B') for n in notes), f'{layout}: у двух заказов магазина 104 — «A · B», одинаковые — один раз')
                check(not page.evaluate("() => window.__xss") and page.evaluate("() => document.querySelectorAll('.dp-stop-note img, .dp-stop-note b').length") == 0,
                      f'{layout}: HTML в комментарии не разобран')
                if shots:
                    page.locator('.dp-stop-note').first.scroll_into_view_if_needed()
                    page.screenshot(path=str(shots / f'plan-{layout}.png'))
                chip = page.locator('#dpBlChip')
                if chip.count():
                    chip.first.click()
                else:
                    page.locator('[data-fold="dpBacklog"], .dp-chipbtn', has_text='Նախորդ օրերից').first.click()
                page.wait_for_selector('#dpBlDlg[open] .dp-bl-row', timeout=10000)
                bl = page.evaluate("() => [...document.querySelectorAll('#dpBlDlg .dp-bl-row .dp-bl-main > span:last-child')].map(n => n.textContent)")
                check(bl == [OLD], f'{layout}: «Նախորդ օրերից» показывает комментарий строки: {bl}')
                check(page.evaluate("() => document.querySelectorAll('#dpBlDlg .dp-bl-main i').length") == 0, f'{layout}: HTML в «Նախորդ օրերից» не разобран')
                if shots:
                    page.screenshot(path=str(shots / f'backlog-{layout}.png'))
                ctx.close()
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'ошибок страницы и консоли нет' + ('' if not errors else f': {errors[:3]}'))
    print(f"\n{sum(results)}/{len(results)} OK")
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

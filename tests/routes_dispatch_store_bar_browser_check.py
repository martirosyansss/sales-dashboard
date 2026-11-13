# -*- coding: utf-8 -*-
"""Полоса магазинов над картой на «Առաքում» (владелец 10.11.2026): вне рейсов, новые и прочие — числом и описанием.

Запуск из корня проекта:  python tests/routes_dispatch_store_bar_browser_check.py [папка для снимков]
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и день — из
tests/routes_dispatch_browser_check.py; ответу /api/routes/dispatch подставлены числа живого дня 10.11. Порт 8791.

A 1920: полоса видна, над рабочим экраном, одна строка, у каждого вида число, название, описание; нулевых нет;
  порядок — красные, жёлтые, остальные; рабочий экран не выходит за окно;
B панель «Օրը» свёрнута — полоса на месте;
C нажатие «… չենք տանում» открывает этот список; у вида без списка кнопки нет;
D новые с прошлой сборки появляются при опросе, без полной перерисовки;
E 1366 и 1101: одна строка, вбок страница не прокручивается, рабочий экран в окне;
F телефон: полосы нет; сохранённый раньше выбор списка не действует.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8791
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
NUMBERS = {'agents_off': 83, 'self_delivery': 31, 'customers_off': 1, 'shipped_before': 14, 'excluded': 1, 'other_vehicle': 1, 'no_coords': 1}
ORDER = {'isn': '00000000-0000-4000-8000-000000000001', 'doc_num': '1', 'order_date': DAY, 'customer_id': 101, 'code': '101', 'name': 'Test',
         'kg': 10, 'revenue': 1000}
TILES = """() => [...document.querySelectorAll('#dpStoreBar .dp-stcell')].map(c => { const r = c.getBoundingClientRect(); return {
    n: c.querySelector('b').textContent, t: c.querySelector('.dp-stcell-t').textContent, d: c.querySelector('.dp-stcell-d').textContent,
    tag: c.tagName, tone: c.classList.contains('is-bad') ? 0 : c.classList.contains('is-warn') ? 1 : 2, top: Math.round(r.top) }; })"""
GEOM = """() => { const b = document.getElementById('dpStoreBar').getBoundingClientRect(), w = document.getElementById('dpWs').getBoundingClientRect();
    return { barBottom: b.bottom, barH: b.height, wsTop: w.top, wsBottom: w.bottom, win: innerHeight,
             side: document.documentElement.scrollWidth - document.documentElement.clientWidth }; }"""


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-storebar-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1920, 'height': 945})
            ctx.add_init_script("try { if (!localStorage.getItem('dpLayout')) localStorage.setItem('dpLayout', 'ws'); } catch (e) {}")
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                ctx.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            day = {}

            def numbers(route):
                resp = route.fetch()
                data = resp.json()
                data.setdefault('orders', {}).update(NUMBERS)
                data['is_past'] = False        # день стенда прошёл, а прошедшие дни страница не опрашивает
                data['excluded'] = [ORDER]     # по одному счётчику раздел не откроется: нужны сами заказы
                data['other_vehicle'] = [ORDER]
                day.update(data)
                route.fulfill(response=resp, body=json.dumps(data), content_type='application/json')
            ctx.route('**/api/routes/dispatch?date=*', numbers)

            def poll(route):
                # опрос без изменений: те же подписи дня, только новые заказы с прошлой сборки (в стенде нет ERP)
                body = {k: day.get(k) for k in ('orders_sig', 'rev', 'crew_sig', 'coord_sig', 'same_day', 'orders_still_coming',
                                                'ready_time', 'built_at', 'data_as_of', 'removed_since_build', 'orders')}
                body.update(success=True, erp_ok=True, new_since_build={'count': 3, 'kg': 100, 'revenue': 1000},
                            removed_since_build={'count': 2, 'kg': 50, 'revenue': 500})
                route.fulfill(status=200, body=json.dumps(body), content_type='application/json')
            ctx.route('**/api/routes/dispatch/status*', poll)

            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)

            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            check(page.locator('#dpStoreBar').is_hidden(), 'A до сборки плана полосы нет')
            page.evaluate("document.getElementById('dpPrepOpen')?.click()")
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.evaluate("document.getElementById('dpBuild').click()")
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.wait_for_selector('#dpStoreBar:not([hidden])', timeout=15000)
            page.reload()                  # ответ сборки не подменён: числа дня — из обычной загрузки
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.wait_for_selector('#dpStoreBar:not([hidden])', timeout=15000)
            page.wait_for_timeout(800)

            # A
            tiles = page.evaluate(TILES)
            names = [t['t'] for t in tiles]
            print('     ', [(t['n'], t['t'], t['d']) for t in tiles])
            check(page.locator('#dpStoreBar').is_visible(), 'A полоса видна')
            check(all(t['n'] and t['t'] and t['d'] for t in tiles) and len(tiles) >= 6, f'A у всех {len(tiles)} видов число, название, описание')
            check(len({t['top'] for t in tiles}) == 1, 'A одна строка')
            check([t['tone'] for t in tiles] == sorted(t['tone'] for t in tiles), 'A красные, жёлтые, остальные')
            check('Հանված երթերից' not in names, 'A нулевого вида нет')
            for name, n in (('Այլ մեքենայով', '1'), ('Հանված մենեջերներ', '83'), ('Մենեջերն է տանում', '31'), ('Արդեն առաքված', '14'), ('Մեքենաները չեն տանում', '1')):
                check(any(t['t'] == name and t['n'] == n for t in tiles), f'A {name} = {n}')
            g = page.evaluate(GEOM)
            check(g['barBottom'] <= g['wsTop'] + 1 and g['wsBottom'] <= g['win'] + 1, f'A полоса над экраном, экран в окне {g}')
            if shots:
                page.screenshot(path=str(shots / 'bar-1920.png'))

            # B
            page.locator('#dpWsTop > .rt-pmin').click()
            page.wait_for_timeout(300)
            check(page.locator('#dpInbox').is_hidden() and page.locator('#dpStoreBar').is_visible(), 'B «Օրը» свёрнута — полоса на месте')
            if shots:
                page.screenshot(path=str(shots / 'bar-collapsed.png'))
            page.locator('.rt-ppill--top').click()
            page.wait_for_timeout(300)

            # C
            check(page.locator('#dpStoreBar div.dp-stcell', has_text='Արդեն առաքված').count() == 1, 'C вид без списка — не кнопка')
            for text, fold in (('Հանված մենեջերներ', 'dpAgents'), ('չենք տանում', 'dpExcluded'), ('Այլ մեքենայով', 'dpOther')):
                page.locator('#dpStoreBar button.dp-stcell', has_text=text).first.click()
                page.wait_for_timeout(700)
                check(page.locator(f'#{fold}').is_visible() and page.evaluate(f"document.getElementById('{fold}').open"), f'C «{text}» открывает {fold}')
                page.locator('#dpGuideClose').click()
                page.wait_for_timeout(300)

            # D
            page.evaluate("(() => { const now = Date.now; Date.now = () => now() + 6 * 60000; })()")   # интервал опроса (POLL_MS) прошёл
            page.evaluate("window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange'))")
            ok = True
            try:
                page.wait_for_function("() => [...document.querySelectorAll('#dpStoreBar .dp-stcell-t')].some(x => x.textContent === 'Կազմումից հետո եկած')", timeout=8000)
            except Exception:
                ok = False
            check(ok, 'D новые с прошлой сборки появились при опросе')
            check(any(t['t'] == 'Հանված երթերից' and t['n'] == '2' for t in page.evaluate(TILES)), 'D снятые с прошлой сборки — тоже')

            # E
            for w, h in ((1366, 768), (1366, 660), (1101, 768)):
                page.set_viewport_size({'width': w, 'height': h})
                page.wait_for_timeout(700)
                tiles = page.evaluate(TILES)
                g = page.evaluate(GEOM)
                wide = page.evaluate("(() => { const b = document.getElementById('dpStoreBar'); return b.scrollWidth - b.clientWidth; })()")
                cut = page.evaluate("[...document.querySelectorAll('#dpStoreBar .dp-stcell-t')].filter(x => x.scrollHeight > x.clientHeight + 1).map(x => x.textContent)")
                print('      overflow', wide, 'cut titles', cut, g)
                # 1101 — самый узкий рабочий экран: девять видов там прокручиваются внутри полосы, страница вбок не уходит
                check(len(tiles) >= 9 and g['side'] <= 0 and (w < 1366 or (wide <= 0 and not cut)), f'E {w}x{h}: {len(tiles)} видов, страница вбок не уходит, от 1366 — без прокрутки и названия целиком')
                check(g['wsBottom'] <= g['win'] + 1 or (h < 700 and g['wsBottom'] - g['win'] <= 40), f'E {w}x{h}: экран в окне (низкое окно — не больше 40 px вниз)')
                if shots:
                    page.screenshot(path=str(shots / f'bar-{w}x{h}.png'))

            # F
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(700)
            check(page.locator('#dpStoreBar').is_hidden(), 'F телефон: полосы нет')
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.evaluate("localStorage.setItem('dpLayout', 'list')")
            page.reload()
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.wait_for_selector('#dpStoreBar:not([hidden])', timeout=15000)
            check(page.locator('#dpStoreBar').is_visible(), 'F старый выбор списка не действует: полоса на месте')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERR  ' + e)
    check(not errors, 'нет ошибок страницы и консоли')
    print(f'{sum(results)}/{len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

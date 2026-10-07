# -*- coding: utf-8 -*-
"""Быстрая правка состава рейса на странице «Развоз» (/routes/dispatch) в настоящем браузере: «×» у магазина и
«Խանութ» у рейса (правка trip_stops).

Запуск из корня проекта:  python tests/routes_dispatch_trip_stops_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py (магазины 101, 102, 104 и 999 без точки); порт 8773 на 127.0.0.1.

A сборка рейсов, карточки машин раскрыты: у каждого магазина рейса — «×», у каждого рейса — «Խանութ»;
B «×» у первого магазина → одна правка trip_stops remove [cid], подсказка «հանվեց» с «Չեղարկել», магазин — в
  «Դեռ երթերում չեն», фокус остался в рейсе («×» соседней строки или «Խանութ»);
C «Չեղարկել» в подсказке → правка undo, магазин снова в рейсе;
D снова «×», затем «Խանութ» у рейса → окно: магазин в группе «Դեռ երթերում չեն», «Ավելացնել» недоступна; поиск по
  коду оставляет одну строку, Enter в поиске ничего не отправляет; галочка → «Ընտրված՝ 1» и «Ավելացնել (1)»; клик → правка add [cid], окно закрыто,
  магазин в этом рейсе;
E окно и Esc → закрыто без запроса правки;
P прошедший день (часы на день позже): ни «×», ни «Խանութ»;
H телефон 390×860, вкладка «Երթեր»: «×» видна, не меньше 36 px и внутри карточки, горизонтальной прокрутки нет.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from route_optimizer import views  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8773
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-trip-stops-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-trip-stops-'), FakeClient())
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)     # «сейчас» — накануне DAY: день не прошедший, правки доступны
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                    if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            data = lambda: page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")  # noqa: E731
            edits = lambda n: [p[1] for p in posts[n:] if p[0] == 'edit']  # noqa: E731

            def trip_of(d, cid):
                return next((tr['id'] for t in d['plan']['trucks'] for tr in t['trips']
                             if any(s['customer_id'] == cid for s in tr['stops'])), None)

            def open_cards():
                for i in range(page.locator('#dpTruckCards .dp-tcard').count()):
                    head = page.locator('#dpTruckCards .dp-tcard').nth(i).locator('.dp-thead')
                    if head.get_attribute('aria-expanded') != 'true':
                        head.click()

            def wait_idle():
                page.wait_for_function("() => !document.body.classList.contains('is-busy') && !document.querySelector('[aria-busy=\"true\"]')",
                                       timeout=15000)
                page.wait_for_timeout(200)

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.click('#dpBuild')
            page.wait_for_selector('#dpBoard .dp-bar', timeout=30000)
            open_cards()
            d = data()
            placed = [s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']]
            xs = page.locator('#dpTruckCards .dp-stop .dp-stop-x')
            check(len(placed) >= 2 and xs.count() == len(placed), f'A «×» at every trip store ({xs.count()} / {len(placed)})')
            trips = sum(len(t['trips']) for t in d['plan']['trucks'])
            check(page.locator('#dpTruckCards .dp-addbtn').count() == trips, f'A «Խանութ» at every trip ({trips})')
            page.screenshot(path=str(SHOTS / 'a-cards.png'), full_page=True)

            # B
            first = page.locator('#dpTruckCards .dp-stop').first
            cid = int(first.get_attribute('data-cid'))
            home = trip_of(d, cid)
            n = len(posts)
            first.locator('.dp-stop-x').click()
            page.wait_for_selector('#dpToast.is-on .dp-toast-act', timeout=15000)
            e = edits(n)
            check(len(e) == 1 and e[0].get('action') == 'trip_stops' and e[0].get('remove') == [cid] and e[0].get('trip') == home,
                  'B one trip_stops remove edit: ' + str(e))
            check('հանվեց' in page.locator('#dpToast').inner_text(), 'B toast says removed, with «Չեղարկել»')
            focused = page.evaluate("() => { const a = document.activeElement; return a ? a.className : ''; }")
            check(focused in ('dp-stop-x', 'rt-btn rt-btn-ghost rt-btn-sm dp-addbtn'), 'B focus stays in the trip: ' + focused)
            d = data()
            check(cid in [s['customer_id'] for s in d['plan']['unassigned']] and trip_of(d, cid) is None, 'B store now not in trips')
            page.screenshot(path=str(SHOTS / 'b-removed.png'), full_page=True)

            # C
            n = len(posts)
            page.locator('#dpToast .dp-toast-act').click()
            page.wait_for_function("(c) => [...document.querySelectorAll('#dpTruckCards .dp-stop')].some(li => li.dataset.cid === String(c))",
                                   arg=cid, timeout=15000)
            check([x.get('action') for x in edits(n)] == ['undo'], 'C undo edit sent')
            check(trip_of(data(), cid) == home, 'C store back in its trip')

            # D
            open_cards()
            page.locator(f'#dpTruckCards .dp-stop[data-cid="{cid}"] .dp-stop-x').click()
            page.wait_for_function("(c) => ![...document.querySelectorAll('#dpTruckCards .dp-stop')].some(li => li.dataset.cid === String(c))",
                                   arg=cid, timeout=15000)
            open_cards()
            d = data()
            target = d['plan']['trucks'][0]['trips'][0]['id']
            page.locator(f'#dpTruckCards .dp-trip[data-trip="{target}"] .dp-addbtn').click()
            dlg = page.locator('#dpAddDlg')
            check(dlg.is_visible(), 'D add dialog open')
            check(page.locator('#dpAddList .dp-add-h').first.inner_text() == 'Դեռ երթերում չեն', 'D first group: not in trips')
            check(page.locator('#dpAddSave').is_disabled(), 'D «Ավելացնել» disabled until a pick')
            code = next(s['code'] for s in d['plan']['unassigned'] if s['customer_id'] == cid)
            page.fill('#dpAddFind', code)
            rows = page.locator('#dpAddList .dp-add-row')
            check(rows.count() == 1, f'D search by code leaves one row ({rows.count()})')
            n = len(posts)
            page.press('#dpAddFind', 'Enter')
            page.wait_for_timeout(300)
            check(dlg.is_visible() and not edits(n), 'D Enter in search does not submit')
            rows.first.locator('input').check()
            check('Ընտրված՝ 1' in page.locator('#dpAddSum').inner_text() and page.locator('#dpAddSave').inner_text().strip() == 'Ավելացնել (1)',
                  'D summary and button count: ' + page.locator('#dpAddSum').inner_text())
            dlg.screenshot(path=str(SHOTS / 'd-dialog.png'))
            n = len(posts)
            page.click('#dpAddSave')
            page.wait_for_function("() => !document.getElementById('dpAddDlg').open", timeout=15000)
            e = edits(n)
            check(len(e) == 1 and e[0].get('action') == 'trip_stops' and e[0].get('add') == [cid] and e[0].get('trip') == target,
                  'D one trip_stops add edit: ' + str(e))
            check(trip_of(data(), cid) == target, 'D store added to the chosen trip')

            # E
            wait_idle()
            open_cards()
            page.locator('#dpTruckCards .dp-addbtn').first.click()
            check(dlg.is_visible(), 'E dialog open again')
            n = len(posts)
            page.keyboard.press('Escape')
            page.wait_for_function("() => !document.getElementById('dpAddDlg').open", timeout=5000)
            check(not edits(n), 'E Esc closes without an edit')

            # P — прошедший день: кнопок быстрой правки нет
            views._clock = lambda: datetime(2026, 10, 2, 10, 0)
            page.reload()
            page.wait_for_selector('#dpTruckCards .dp-tcard', timeout=30000)
            open_cards()
            check(page.locator('#dpTruckCards .dp-stop').count() > 0 and page.locator('.dp-stop-x, .dp-addbtn').count() == 0,
                  'P past day: no «×» and no «Խանութ»')
            views._clock = lambda: datetime(2026, 9, 30, 18, 0)
            page.reload()
            page.wait_for_selector('#dpTruckCards .dp-tcard', timeout=30000)

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(400)
            page.click('.dp-tab[data-tab="trips"]')         # №81: на телефоне рейсы — во вкладке «Երթեր»
            page.wait_for_timeout(300)
            open_cards()
            x = page.locator('#dpTruckCards .dp-stop-x').first
            box = x.bounding_box()
            card = page.locator('#dpTruckCards .dp-tcard').first.bounding_box()
            check(bool(box) and box['width'] >= 36 and box['x'] + box['width'] <= card['x'] + card['width'],
                  f'H phone: «×» ≥36 px and inside the card ({box})')
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(over <= 0, f'H phone: no horizontal scroll ({over}px)')
            check(page.locator('#dpTruckCards .dp-stop-x').first.is_visible(), 'H phone: «×» visible')
            page.screenshot(path=str(SHOTS / 'h-phone.png'), full_page=True)
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'no page/console errors' + (': ' + '; '.join(errors[:5]) if errors else ''))
    print(f'\nscreenshots: {SHOTS}')
    print('ALL OK' if all(results) else f'FAILED {results.count(False)} of {len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

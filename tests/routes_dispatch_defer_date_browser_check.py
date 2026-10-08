# -*- coding: utf-8 -*-
"""«Այսօր չենք տանում» — «Երբ տանել» (владелец 08.10) на странице «Развоз» (/routes/dispatch) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_defer_date_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py (магазины 101, 102, 104 и 999 без точки), «сейчас» — вечер 30.09, день
плана — чт 01.10; база — временная; порт 8775 на 127.0.0.1.

A сборка рейсов; «×» у магазина → окно «Ինչու՞»: под «Այսօր չենք տանում» — группа «Երբ տանել» (radiogroup): дни
  defer_days и «Չգիտեմ», выбран первый, он же «Վաղը, …»; подсказка — в какой день войдут заказы;
B выбор дня (пн) сам отмечает «Այսօր չենք տանում» и открывает «Հանել երթից»; сохранение — одна правка defer_store
  {customer_id, to}, подсказка «կտանենք», магазина в рейсах нет, в «Այսօր չենք տանում» — значок «կտանենք …»,
  фокус остался в рейсе; клавиатура: стрелка в группе дней переключает день;
C другой магазин, «Չգիտեմ» → defer_store с to = null, подсказка про «Նախորդ օրերից»;
D следующий понедельник (05.10): заказ магазина из B — в развозе дня, «տեղափոխված է 1 հոկտեմբերի պլանից»;
H телефон 390×860: в окне чипы переносятся, горизонтальной прокрутки нет ни в окне, ни на странице.
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
from route_optimizer.actuals import YEREVAN  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8775
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-defer-date-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-defer-date-'), FakeClient())
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)     # «сейчас» — накануне DAY: день не прошедший, правки доступны
    views._yerevan_now = lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN)
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
            data = lambda day=DAY: page.evaluate("(d) => fetch('/api/routes/dispatch?date=' + d).then(r => r.json())", day)  # noqa: E731
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

            chips = page.locator('#dpWhyOpts .dp-why-chip')

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.click('#dpBuild')
            page.wait_for_selector('#dpBoard .dp-bar', timeout=30000)
            open_cards()
            d = data()
            days = d['defer_days']
            check(days == ['2026-10-02', '2026-10-03', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08'],
                  'A defer_days: workdays within a week ' + str(days))
            first = page.locator('#dpTruckCards .dp-stop').first
            cid = int(first.get_attribute('data-cid'))
            first.locator('.dp-stop-x').click()
            group = page.locator('#dpWhyOpts [role="radiogroup"]')
            check(group.count() == 1 and group.get_attribute('aria-label') == 'Երբ տանել', 'A radiogroup «Երբ տանել» under the reason')
            check(chips.count() == len(days) + 1 and chips.last.inner_text().strip() == 'Չգիտեմ',
                  f'A one chip per day + «Չգիտեմ» ({chips.count()})')
            check(chips.first.locator('input').is_checked() and chips.first.inner_text().startswith('Վաղը, '),
                  'A first day picked by default and named «Վաղը»: ' + chips.first.inner_text())
            check('Երբ տանել՝' in page.locator('#dpWhyOpts .dp-why-days-t').inner_text(), 'A caption «Երբ տանել՝»')
            check('պլանի մեջ' in page.locator('#dpWhyDaysHint').inner_text(), 'A hint names the day: ' + page.locator('#dpWhyDaysHint').inner_text())
            opt = page.locator('#dpWhyOpts .dp-why-opt', has=page.locator('input[value="not_today"]'))
            check('Ընտրեք՝ երբ տանել' in opt.inner_text(), 'A option text explains the choice')
            page.locator('#dpWhyDlg').screenshot(path=str(SHOTS / 'a-why.png'))

            # B
            n = len(posts)
            check(not page.locator('#dpWhyOpts input[value="not_today"]').is_checked() and page.locator('#dpWhySave').is_disabled(),
                  'B no reason yet: «Հանել երթից» disabled')
            chips.nth(2).click()                                   # пн 05.10
            check(page.locator('#dpWhyOpts input[value="not_today"]').is_checked() and not page.locator('#dpWhySave').is_disabled(),
                  'B picking a day checks «Այսօր չենք տանում» and enables save')
            chips.nth(2).locator('input').focus()
            page.keyboard.press('ArrowRight')
            check(chips.nth(3).locator('input').is_checked(), 'B arrow key moves to the next day')
            page.keyboard.press('ArrowLeft')
            check(chips.nth(2).locator('input').is_checked() and not edits(n), 'B arrow back, nothing sent yet')
            page.click('#dpWhySave')
            page.wait_for_function("() => !document.getElementById('dpWhyDlg').open", timeout=15000)
            wait_idle()
            e = edits(n)
            check(e == [{'date': DAY, 'rev': e[0].get('rev') if e else None, 'action': 'defer_store', 'customer_id': cid, 'to': '2026-10-05'}],
                  'B one defer_store edit: ' + str(e))
            check('կտանենք' in page.locator('#dpToast').inner_text() and 'հոկտեմբերի' in page.locator('#dpToast').inner_text(),
                  'B toast: ' + page.locator('#dpToast').inner_text())
            focused = page.evaluate("() => { const a = document.activeElement; return a ? a.className : ''; }")
            check(focused in ('dp-stop-x', 'rt-btn rt-btn-ghost rt-btn-sm dp-addbtn', 'dp-toast-act'), 'B focus stays in the trip: ' + focused)
            d = data()
            check(trip_of(d, cid) is None and all(o.get('later_to') == '2026-10-05' for o in d['excluded'] if o['customer_id'] == cid)
                  and any(o['customer_id'] == cid for o in d['excluded']), 'B store out of trips, its orders excluded with later_to')
            badge = page.locator('#dpExcludedList .dp-later')
            check(badge.count() >= 1 and badge.first.inner_text().startswith('կտանենք'), 'B «կտանենք …» badge in the excluded list')
            page.screenshot(path=str(SHOTS / 'b-deferred.png'), full_page=True)

            # C
            wait_idle()
            open_cards()
            other = page.locator('#dpTruckCards .dp-stop').first
            cid2 = int(other.get_attribute('data-cid'))
            other.locator('.dp-stop-x').click()
            n = len(posts)
            chips.last.click()
            check('Նախորդ օրերից' in page.locator('#dpWhyDaysHint').inner_text(), 'C «Չգիտեմ» hint: stays in previous days')
            page.click('#dpWhySave')
            page.wait_for_function("() => !document.getElementById('dpWhyDlg').open", timeout=15000)
            wait_idle()
            e = edits(n)
            check(len(e) == 1 and e[0].get('action') == 'defer_store' and e[0].get('customer_id') == cid2 and e[0].get('to') is None,
                  'C «Չգիտեմ» → defer_store to null: ' + str(e))
            check('Նախորդ օրերից' in page.locator('#dpToast').inner_text(), 'C toast: ' + page.locator('#dpToast').inner_text())
            check(trip_of(data(), cid2) is None, 'C store out of trips')

            # D
            mon = data('2026-10-05')
            rows = [o for o in mon['backlog'] if o['customer_id'] == cid]
            check(rows and all(o['taken'] and o['carried'] and o.get('carried_from') == DAY for o in rows),
                  'D on Monday the store orders are in the day: ' + str(rows))
            page.goto(f'{BASE}/routes/dispatch?date=2026-10-05')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_timeout(500)
            check('1 հոկտեմբերի պլանից' in (page.locator('#dpBacklogList').text_content() or ''), 'D backlog row says moved from the 1 Oct plan')
            page.screenshot(path=str(SHOTS / 'd-monday.png'), full_page=True)

            # H
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpTruckCards .dp-tcard', timeout=30000)
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(400)
            page.click('.dp-tab[data-tab="trips"]')
            page.wait_for_timeout(300)
            open_cards()
            page.locator('#dpTruckCards .dp-stop-x').first.click()
            page.locator('#dpWhyOpts input[value="not_today"]').check()
            dlg = page.locator('#dpWhyDlg')
            over = dlg.evaluate('(el) => el.scrollWidth - el.clientWidth')
            box = dlg.bounding_box()
            inside = all((b := chips.nth(i).bounding_box()) and b['x'] >= box['x'] and b['x'] + b['width'] <= box['x'] + box['width'] + 0.5
                         for i in range(chips.count()))
            check(over <= 0 and inside, f'H phone: chips wrap inside the dialog (overflow {over}px)')
            page.screenshot(path=str(SHOTS / 'h-phone.png'))
            page.keyboard.press('Escape')
            pover = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(pover <= 0, f'H phone: no horizontal page scroll ({pover}px)')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'no page/console errors' + (': ' + '; '.join(errors[:5]) if errors else ''))
    print(f'\nscreenshots: {SHOTS}')
    print('ALL OK' if all(results) else f'FAILED {results.count(False)} of {len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

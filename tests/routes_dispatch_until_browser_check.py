# -*- coding: utf-8 -*-
"""«Մինչև ժամը» (владелец 08.10) на странице «Развоз» (/routes/dispatch) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_until_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py (магазины 101, 102, 104 и 999 без точки), «сейчас» — вечер 30.09, день
плана — чт 01.10; база — временная; порт 8778 на 127.0.0.1.

A сборка рейсов, режим «Փոփոխել»: в строке последнего магазина рейса — кнопка «Մինչև ժամը» (после «Այսօր չենք տանում»);
  диалог: магазин, поле времени, «Միայն …» (день плана — не сегодня: «Միայն հինգշաբթի, 1 հոկտեմբերի») выбрано, «Միշտ»,
  подсказка о постоянном окне, «Հանել» скрыт (снимать нечего); пустое время — ошибка, запроса нет;
B 09:40 «Միայն …» → одна правка until {customer_id, time, scope: day}; магазин — первым в рейсе, плашка «մինչև 09:40 ·
  միայն այս օրը» (не красная), уведомление «Երթի հերթականությունը փոխվեց»;
C повторно: время подставлено, «Հանել» виден; у магазина постоянное окно другого вида — при «Միշտ» предупреждение,
  «Հանել» для «Միշտ» скрыт; Esc закрывает без запроса;
D 09:05 — не успеть никому: плашка красная «չի հասցնում՝ …», уведомление «Չի հասցնում»;
E «Հանել» (срок дня) → until с time null; плашки срока дня больше нет, снова постоянное окно;
H телефон 390×860: в диалоге и на странице нет горизонтальной прокрутки.
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
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8778
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-until-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-until-'), FakeClient())
    store = app.extensions['route_optimizer'].store
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
            edits = lambda n: [p[1] for p in posts[n:] if p[0] == 'edit']  # noqa: E731
            dlg = page.locator('#dpUntilDlg')

            def open_cards():
                for i in range(page.locator('#dpTruckCards .dp-tcard').count()):
                    head = page.locator('#dpTruckCards .dp-tcard').nth(i).locator('.dp-thead')
                    if head.get_attribute('aria-expanded') != 'true':
                        head.click()

            def wait_idle():
                page.wait_for_function("() => !document.querySelector('[aria-busy=\"true\"]')", timeout=15000)
                page.wait_for_timeout(250)

            def editing():
                """Режим «Փոփոխել» у рейса (после перерисовки — снова, если сброшен)."""
                open_cards()
                if not page.locator('#dpTruckCards .dp-trip.is-editing').count():
                    page.locator('#dpTruckCards .dp-editbtn').first.click()

            def row(cid):
                return page.locator(f'#dpTruckCards .dp-trip.is-editing .dp-stop[data-cid="{cid}"]').first

            def open_until(cid):
                editing()
                row(cid).locator('.dp-untilbtn').click()
                page.wait_for_function("() => !document.getElementById('dpUntilSave').disabled", timeout=15000)

            def save_and_wait():
                page.click('#dpUntilSave')
                page.wait_for_function("() => !document.getElementById('dpUntilDlg').open", timeout=15000)
                wait_idle()

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.click('#dpBuild')
            page.wait_for_selector('#dpBoard .dp-bar', timeout=30000)
            editing()
            cids = [int(x) for x in page.locator('#dpTruckCards .dp-trip.is-editing .dp-stop').evaluate_all(
                '(els) => els.map(e => e.dataset.cid)')]
            cid = cids[-1]
            acts = row(cid).locator('.dp-stop-acts button').all_inner_texts()
            check('Մինչև ժամը' in acts and acts.index('Մինչև ժամը') == acts.index('Այսօր չենք տանում') + 1,
                  'A «Մինչև ժամը» after «Այսօր չենք տանում»: ' + str(acts))
            check(row(cid).locator('.dp-untilbtn i.fa-clock').count() == 1, 'A clock icon')
            open_until(cid)
            check(dlg.is_visible() and page.locator('#dpUntilLead').inner_text().startswith('«'), 'A dialog with the store name')
            check(page.locator('#dpUntilDay').is_checked() and not page.locator('#dpUntilAlways').is_checked(),
                  'A «Միայն …» is the default')
            check(page.locator('#dpUntilDayT').inner_text() == 'Միայն հինգշաբթի, 1 հոկտեմբերի',
                  'A day label names the plan day: ' + page.locator('#dpUntilDayT').inner_text())
            check('Մշտական ընդունման ժամ նշված չէ' in page.locator('#dpUntilHint').inner_text(), 'A hint: no permanent window')
            check(page.locator('#dpUntilClear').is_hidden(), 'A «Հանել» hidden — nothing to clear')
            n = len(posts)
            page.click('#dpUntilSave')
            check(page.locator('#dpUntilErr').inner_text() == 'Նշեք ժամը։' and not edits(n), 'A empty time — error, no request')
            dlg.screenshot(path=str(SHOTS / 'a-dialog.png'))

            # B
            page.fill('#dpUntilTime', '09:40')
            n = len(posts)
            save_and_wait()
            e = edits(n)
            check(len(e) == 1 and {k: e[0].get(k) for k in ('action', 'customer_id', 'time', 'scope')}
                  == {'action': 'until', 'customer_id': cid, 'time': '09:40', 'scope': 'day'}, 'B one until edit: ' + str(e))
            editing()
            first = int(page.locator('#dpTruckCards .dp-trip.is-editing .dp-stop').first.get_attribute('data-cid'))
            check(first == cid, f'B the store is first in its trip now ({first})')
            tag = row(cid).locator('.rt-badge.dp-b-until')
            check(tag.count() == 1 and tag.inner_text().strip() == 'մինչև 09:40 · միայն այս օրը',
                  'B day tag: ' + (tag.inner_text() if tag.count() else '—'))
            check('Երթի հերթականությունը փոխվեց' in page.locator('#dpToast').inner_text(), 'B toast: ' + page.locator('#dpToast').inner_text())
            page.screenshot(path=str(SHOTS / 'b-reordered.png'), full_page=True)

            # C
            store.save_customer_window(cid, st.CustomerWindow('between', 600, 720), 'qa')
            open_until(cid)
            check(page.locator('#dpUntilTime').input_value() == '09:40' and page.locator('#dpUntilClear').is_visible(),
                  'C time prefilled, «Հանել» visible')
            hint = page.locator('#dpUntilHint').inner_text()
            check('Այս օրվա համար նշված է՝ մինչև 09:40' in hint and '10:00–12:00' in hint and 'կփոխարինի' not in hint, 'C hint: ' + hint)
            page.check('#dpUntilAlways')
            check('Ուշադրություն՝ «Միշտ»-ը կփոխարինի այն' in page.locator('#dpUntilHint').inner_text()
                  and page.locator('#dpUntilClear').is_hidden(), 'C «Միշտ»: warning, nothing to clear for a «between» window')
            dlg.screenshot(path=str(SHOTS / 'c-always.png'))
            n = len(posts)
            page.keyboard.press('Escape')
            page.wait_for_timeout(200)
            check(not dlg.evaluate('(d) => d.open') and not edits(n), 'C Esc closes without a request')

            # D
            open_until(cid)
            page.fill('#dpUntilTime', '09:05')
            save_and_wait()
            editing()
            red = row(cid).locator('.rt-badge.b-danger')
            check(red.count() >= 1 and red.first.inner_text().strip().startswith('չի հասցնում՝ մինչև 09:05'),
                  'D red tag: ' + (red.first.inner_text() if red.count() else '—'))
            check('Չի հասցնում' in page.locator('#dpToast').inner_text(), 'D toast: ' + page.locator('#dpToast').inner_text())
            page.screenshot(path=str(SHOTS / 'd-late.png'), full_page=True)

            # E
            open_until(cid)
            n = len(posts)
            page.click('#dpUntilClear')
            page.wait_for_function("() => !document.getElementById('dpUntilDlg').open", timeout=15000)
            wait_idle()
            e = edits(n)
            check(len(e) == 1 and e[0].get('action') == 'until' and e[0].get('time') is None and e[0].get('scope') == 'day',
                  'E clear → until with time null: ' + str(e))
            editing()
            check(row(cid).locator('.dp-b-until').count() == 0 and 'ընդունում է՝ 10:00–12:00' in row(cid).inner_text()
                  or 'չի հասցնում՝ 10:00–12:00' in row(cid).inner_text(), 'E day tag gone, permanent window back')
            check(store.load().day_until == {}, 'E day deadline removed in the store')

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(400)
            page.click('.dp-tab[data-tab="trips"]')
            page.wait_for_timeout(300)
            open_until(cid)
            over = dlg.evaluate('(el) => el.scrollWidth - el.clientWidth')
            check(over <= 0, f'H phone: dialog without horizontal scroll ({over}px)')
            dlg.screenshot(path=str(SHOTS / 'h-phone.png'))
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

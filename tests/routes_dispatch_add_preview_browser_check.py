# -*- coding: utf-8 -*-
"""Браузерная проверка цены правки заранее (запуск вручную, Playwright; ERP — поддельная, базы — временные).

Окно «Նախորդ օրերից»: строка цены под заказом, галочки и итог внизу, «Տանել նշվածները», тихий отказ расчёта, заметка при
утверждённом плане. Мастер: на шаге «Պատվեր» карточка без кнопки, на шаге «Երթեր» — с «Որոշել». Окно «×» — «Եթե հանեք».
Снимки: <temp>/dispatch-add-preview.png (окно с галочками), dispatch-add-preview-wizard.png (карточка шага «Երթեր»)
"""
from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conftest  # noqa: E402,F401  все пути баз — во временную папку
from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _isn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8797
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
OLD = date(2026, 9, 28)
SHOT = Path(tempfile.gettempdir()) / 'dispatch-add-preview.png'
DEMO_ISN = _isn(6)
SAME_ISN = _isn(8)
THIRD_ISN = _isn(7)


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-add-preview-'), FakeClient())
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(6, 104, 70.0, day=OLD), _dorder(7, 103, 55.0, day=OLD),
              _dorder(8, 101, 40.0, day=OLD)]
    with app.test_request_context():
        class _C:       # _dispatch_setup ждёт test-client
            application = app
            post = staticmethod(lambda url, json=None: app.test_client().post(url, json=json))
        _dispatch_setup(_C, orders)
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)
    views._yerevan_now = lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 889}).new_page()
            page.add_init_script("(() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();")
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                    if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)

            # --- шаг «Պատվեր» до сборки: только сведение, без кнопки ---
            page.wait_for_selector('#dpWizard[open]', timeout=15000)
            # шаг «Պատվեր» мастера (12.11.2026): сводка — строка «Նախորդ օրերից» без кнопки; напоминания панели (dpAttn) там скрыты
            item = page.locator('#dpGuideSummary .dp-gsum-part.is-later .dp-gsum-row', has_text='Նախորդ օրերից')
            check(item.count() == 1 and 'Կորոշեք «Երթեր» քայլում, երբ կտեսնեք, թե որտեղ են տեղավորվում' in item.inner_text(),
                  'wizard step 1: info text')
            check('3' in item.inner_text() and item.locator('button').count() == 0, 'wizard step 1: no button on the row')
            page.click('#dpGuideClose')
            page.wait_for_selector('#dpWizard:not([open])', state='attached', timeout=5000)

            # --- план собран: кнопка — на шаге «Երթեր» ---
            page.evaluate("""(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())
                .then(d => fetch('/api/routes/dispatch/build', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: day, trucks: d.trucks.map(t => t.car_code)})}))""", DAY)
            page.reload()
            page.wait_for_selector('.dp-stop .dp-stop-x', state='attached', timeout=30000)
            page.click('#dpWizardStart')
            page.wait_for_selector('#dpWizard[open]', timeout=5000)
            page.click('#dpWizard [data-guide-step="2"]')
            card = page.locator('#dpGuideBacklog')
            card.wait_for(state='visible', timeout=5000)
            check('3' in card.inner_text() and 'չառաքված պատվեր' in card.inner_text(), 'wizard step 3: card with the count')
            check(card.locator('button').inner_text().strip() == 'Որոշել', 'wizard step 3: button «Որոշել»')
            card.screenshot(path=str(SHOT.with_name('dispatch-add-preview-wizard.png')))
            page.click('#dpWizard [data-guide-step="0"]')
            item = page.locator('#dpGuideSummary .dp-gsum-row', has_text='Նախորդ օրերից')
            check(item.count() == 1 and item.locator('button').count() == 0, 'wizard step 1 after build: still no button')
            page.click('#dpWizard [data-guide-step="2"]')
            page.click('#dpGuideBacklog button')
            page.wait_for_selector('#dpBlDlg[open]', timeout=5000)
            n = len(posts)

            # --- строки окна: цена заказа ---
            row = lambda isn: page.locator(f'#dpBlDlg li[data-isn="{isn}"]')  # noqa: E731
            cost = lambda isn: row(isn).locator('.dp-bl-cost').inner_text()  # noqa: E731
            page.wait_for_function("isn => document.querySelector(`#dpBlDlg li[data-isn=\"${isn}\"] .dp-bl-cost`).textContent.includes('կմ')", arg=DEMO_ISN, timeout=15000)
            line = cost(DEMO_ISN)
            check(bool(re.match(r'^Երթ \d+ · \S+ · [+−]?[\d,]+\xa0կմ · [+−]?\d+\xa0րոպե · [+−]?[\d\xa0]+\xa0֏$', line)), 'row line, existing trip: ' + line)
            check(cost(SAME_ISN) == 'Խանութն արդեն երթում է՝ ճանապարհը չի փոխվում', 'row line, store already on a trip: ' + cost(SAME_ISN))
            calls = [p for p in posts if p[0] == 'add-preview']
            check(len(calls) == 1 and 'orders' not in calls[0][1], f'one preview call on open, without orders: {len(calls)}')
            check(page.locator('#dpBlBar').is_hidden(), 'bar hidden while nothing is marked')

            # --- галочки: итог обновляется, запросы с паузой, старые ответы не применяются ---
            n = len(posts)
            row(DEMO_ISN).locator('.dp-bl-cb').check()
            row(THIRD_ISN).locator('.dp-bl-cb').check()
            page.wait_for_function(r"() => /Նշված է 2\sպատվեր · [+−0]/.test(document.getElementById('dpBlBarText').textContent)", timeout=15000)
            bar = page.locator('#dpBlBarText').inner_text()
            check(page.locator('#dpBlBar').is_visible() and 'կմ' in bar and 'րոպե' in bar and '֏' in bar, 'bar totals: ' + bar)
            page.locator('#dpBlDlg').screenshot(path=str(SHOT))
            sel = [p[1] for p in posts[n:] if p[0] == 'add-preview']
            check(len(sel) == 1 and set(sel[0]['orders']) == {DEMO_ISN, THIRD_ISN}, f'debounced: one selection request for two ticks: {sel}')
            one = page.evaluate("""(a) => fetch('/api/routes/dispatch?date=' + a.day).then(r => r.json()).then(d =>
                fetch('/api/routes/dispatch/add-preview', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: a.day, rev: d.rev, orders: a.orders})}).then(r => r.json())).then(r => r.selection)""",
                                {'day': DAY, 'orders': [DEMO_ISN, THIRD_ISN]})
            check(str(one['delta_amd']) in bar.replace('\xa0', ''), f'bar equals the server selection: {one}')
            row(THIRD_ISN).locator('.dp-bl-cb').uncheck()
            page.wait_for_function(r"() => /Նշված է 1\sպատվեր · [+−0]/.test(document.getElementById('dpBlBarText').textContent)", timeout=15000)
            check('Նշված է 1 պատվեր' in page.locator('#dpBlBarText').inner_text(), 'bar follows the ticks')
            row(THIRD_ISN).locator('.dp-bl-cb').check()
            page.wait_for_function(r"() => /Նշված է 2\sպատվեր · [+−0]/.test(document.getElementById('dpBlBarText').textContent)", timeout=15000)

            # --- «Տանել նշվածները» ---
            n = len(posts)
            page.click('#dpBlBarTake')
            page.wait_for_function("isn => document.querySelector(`#dpBlDlg li[data-isn=\"${isn}\"]`).classList.contains('is-taken')", arg=DEMO_ISN, timeout=15000)
            edits = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(edits) == 1 and edits[0].get('action') == 'include' and set(edits[0]['orders']) == {DEMO_ISN, THIRD_ISN}, f'take-selected = one include: {edits}')
            check(row(THIRD_ISN).evaluate("e => e.classList.contains('is-taken')"), 'both rows are taken')
            page.wait_for_function("() => document.getElementById('dpBlBar').hidden", timeout=5000)
            check(row(SAME_ISN).locator('.dp-bl-cb').count() == 1 and row(DEMO_ISN).locator('.dp-bl-cb').count() == 0, 'decided rows lose the checkbox')
            page.wait_for_function("isn => !document.querySelector(`#dpBlDlg li[data-isn=\"${isn}\"] .dp-bl-cost`).textContent.includes('Հաշվում')", arg=SAME_ISN, timeout=15000)
            check(len([p for p in posts[n:] if p[0] == 'add-preview']) >= 1, 'plan change while the dialog is open re-runs the preview')

            # --- отказ расчёта: тихо, кнопки работают ---
            page.click('#dpBlClose')
            errors_before = list(errors)
            page.route('**/api/routes/dispatch/add-preview', lambda r: r.fulfill(status=500, content_type='application/json', body='{}'))
            page.evaluate("document.querySelector('#dpTodo [data-chip=\"backlog\"], #dpBlChip').click()")
            page.wait_for_selector('#dpBlDlg[open]', timeout=5000)
            page.wait_for_function("isn => document.querySelector(`#dpBlDlg li[data-isn=\"${isn}\"] .dp-bl-cost`).textContent.includes('հասանելի չէ')", arg=SAME_ISN, timeout=10000)
            btn = row(SAME_ISN).locator('.dp-bl-act button').first
            check(cost(SAME_ISN) == 'Հաշվարկը հասանելի չէ' and btn.is_enabled(), 'quiet failure, row buttons still work')
            errors[:] = errors_before                                       # 500 расчёта — намеренно
            page.unroute('**/api/routes/dispatch/add-preview')
            page.click('#dpBlClose')

            # --- 429: один повтор через 700 мс, затем — тихая надпись ---
            URL = '**/api/routes/dispatch/add-preview'
            settled = "isn => !document.querySelector(`#dpBlDlg li[data-isn=\"${isn}\"] .dp-bl-cost`).textContent.includes('Հաշվում')"

            def open_bl():
                page.evaluate("document.querySelector('#dpTodo [data-chip=\"backlog\"], #dpBlChip').click()")
                page.wait_for_selector('#dpBlDlg[open]', timeout=5000)
                page.wait_for_function(settled, arg=SAME_ISN, timeout=15000)

            hits = []

            def busy(route, times):
                hits.append(1)
                if len(hits) <= times:
                    route.fulfill(status=429, content_type='application/json', body='{"success": false, "error": "busy"}')
                else:
                    route.continue_()

            errors_before = list(errors)
            page.route(URL, lambda r: busy(r, 1))
            open_bl()
            check(len(hits) == 2 and 'հասանելի չէ' not in cost(SAME_ISN), f'429 once: one retry, then the price: {len(hits)} calls')
            page.unroute(URL)
            page.click('#dpBlClose')
            hits.clear()
            page.route(URL, lambda r: busy(r, 9))
            open_bl()
            check(len(hits) == 2 and cost(SAME_ISN) == 'Հաշվարկը հասանելի չէ', f'429 twice: no more retries, quiet text: {len(hits)} calls')
            page.unroute(URL)
            page.click('#dpBlClose')
            errors[:] = errors_before                                       # 429 — намеренно

            # --- подпись отказа: «Հնարավոր չէ» — запрет, «Տեղ չկա» — нет места, времени или машины ---
            for reason, extra, word in (('պատվերն այս օրվա համար պահեստում հասանելի չէ', {'refused': True, 'cannot': True}, 'Հնարավոր չէ'),
                                        ('մեքենան արդեն բեռնվում է կամ ճանապարհին է', {'refused': True, 'cannot': True}, 'Հնարավոր չէ'),
                                        ('խանութի տեղը քարտեզում նշված չէ', {'cannot': True}, 'Հնարավոր չէ'),
                                        ('բեռը չի տեղավորվում մեքենաներում', {}, 'Տեղ չկա'),
                                        ('աշխատանքային օրում ժամանակ չի մնում', {}, 'Տեղ չկա')):
                body = json.dumps({'success': True, 'rev': 0, 'items': [{
                    'customer_id': 101, 'isns': [SAME_ISN], 'kg': 40, 'status': 'no_room', 'truck': None, 'trip_id': None, 'trip_no': None,
                    'reason': reason, 'delta_km': 0.0, 'delta_min': 0, 'delta_amd': 0, **extra}]})
                page.route(URL, (lambda b: lambda r: r.fulfill(status=200, content_type='application/json', body=b))(body))   # обработчик с двумя аргументами получил бы request
                open_bl()
                check(cost(SAME_ISN) == word + '՝ ' + reason, 'refusal label: ' + cost(SAME_ISN))
                page.unroute(URL)
                page.click('#dpBlClose')

            # --- расширили окно дней при открытом окне — цена считается заново ---
            open_bl()
            previews = lambda: len([p for p in posts if p[0] == 'add-preview'])  # noqa: E731
            n = previews()
            page.select_option('#dpBacklogDays', str(int(page.eval_on_selector('#dpBacklogDays', 'e => e.value')) + 1))
            for _ in range(60):
                if previews() > n:
                    break
                page.wait_for_timeout(200)
            check(previews() > n, f'widening the window re-runs the preview: {previews() - n} call(s)')
            page.wait_for_function(settled, arg=SAME_ISN, timeout=15000)
            page.click('#dpBlClose')

            # --- «×»: сколько сбережёт ---
            page.evaluate("() => document.querySelector('.dp-stop[data-cid] .dp-stop-x').click()")
            page.wait_for_function("() => document.getElementById('dpWhyDlg').open", timeout=10000)
            gain = page.locator('#dpWhyGain')
            check(gain.is_visible() and gain.inner_text().startswith('Եթե հանեք՝ ') and 'կմ' in gain.inner_text() and '֏' in gain.inner_text(), 'why-remove: saving at the top: ' + gain.inner_text())
            page.evaluate("() => document.getElementById('dpWhyDlg').close()")

            # --- утверждённый план: заметка ---
            page.evaluate("""(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())
                .then(d => fetch('/api/routes/dispatch/edit', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: day, rev: d.rev, action: 'approve', confirm_crew: true})}))""", DAY)
            page.reload()
            page.wait_for_selector('.dp-stop .dp-stop-x', state='attached', timeout=30000)
            page.evaluate("document.querySelector('#dpBlChip, #dpTodo [data-chip=\"backlog\"]').click()")
            page.wait_for_selector('#dpBlDlg[open]', timeout=5000)
            note = page.locator('#dpBlApproved')
            check(note.is_visible() and 'Պլանը հաստատված է' in note.inner_text(), 'approved plan: note in the dialog')
            check(not errors, 'no page or console errors: ' + str(errors[:3]))
            browser.close()
    finally:
        server.shutdown()
    print('RESULT', sum(results), '/', len(results), 'screenshot', SHOT)
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

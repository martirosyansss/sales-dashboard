"""Браузерная проверка варианта «Հանել առաքումից» в окне «×» (запуск вручную, Playwright; ERP — поддельная, базы — временные).

Вариант первый в списке, подсказка про день у «Այսօր չենք տանում» при нём пуста, одна правка defer_store с to = null,
магазин уходит из рейсов, кнопка сохранения видна на экране высотой 889 px.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conftest  # noqa: E402,F401  все пути баз — во временную папку
from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8791
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOT = Path(tempfile.gettempdir()) / 'dispatch-why-remove.png'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-why-remove-'), FakeClient())
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
            page.evaluate("""(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())
                .then(d => fetch('/api/routes/dispatch/build', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: day, trucks: d.trucks.map(t => t.car_code)})}))""", DAY)
            page.reload()
            page.wait_for_selector('.dp-stop .dp-stop-x', state='attached', timeout=30000)
            data = lambda: page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")  # noqa: E731
            stops = lambda d: {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}  # noqa: E731
            placed = stops(data())
            cid = page.evaluate("() => { const s = document.querySelector('.dp-stop[data-cid]'); s.querySelector('.dp-stop-x').click(); return Number(s.dataset.cid); }")
            page.wait_for_function("() => document.getElementById('dpWhyDlg').open", timeout=10000)
            opts = page.locator('#dpWhyOpts .dp-why-opt')
            check(opts.count() == 6, 'six reasons')
            check(opts.first.locator('b').first.inner_text().strip() == 'Հանել առաքումից', 'new option is first')
            check(page.locator('#dpWhySave').is_disabled(), 'save disabled until a reason is picked')
            check('պլանի մեջ' in page.locator('#dpWhyDaysHint').inner_text(), 'day hint shown before a reason is picked')
            n = len(posts)
            page.locator('#dpWhyOpts input[value="remove"]').check()
            check(not page.locator('#dpWhySave').is_disabled(), 'picking it enables save')
            check(page.locator('#dpWhyDaysHint').inner_text().strip() == '', 'day hint is empty for this option')
            bottom = page.evaluate("() => [document.getElementById('dpWhySave').getBoundingClientRect().bottom, document.getElementById('dpWhyDlg').getBoundingClientRect().bottom]")
            check(bottom[0] <= bottom[1], f'save button inside the visible dialog: {bottom}')
            page.locator('#dpWhyDlg').screenshot(path=str(SHOT))
            page.click('#dpWhySave')
            page.wait_for_function("() => !document.getElementById('dpWhyDlg').open", timeout=15000)
            page.wait_for_selector('#dpToast.is-on', timeout=15000)
            edits = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(edits) == 1 and edits[0].get('action') == 'defer_store' and edits[0].get('customer_id') == cid
                  and 'to' in edits[0] and edits[0]['to'] is None, 'one defer_store edit with to = null: ' + str(edits))
            check('հանվեց վաղվա առաքումից' in page.locator('#dpToast').inner_text(), 'toast confirms the removal')
            check(cid in placed and cid not in stops(data()), f'store {cid} left the trips')
            check(not errors, 'no page or console errors: ' + str(errors[:3]))
            browser.close()
    finally:
        server.shutdown()
    print('RESULT', sum(results), '/', len(results))
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Реальный браузер, синтетический ERP и временная база; проверка источника/времени и обновления точки."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from routes_dispatch_browser_check import build_app, FakeClient, TILE_PNG, is_ignorable
from test_route_optimizer import make_snapshot
from route_optimizer import erp, views
from route_optimizer.snapshot import SnapshotCache
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'test-artifacts' / 'store-coordinates')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app = build_app(tempfile.mkdtemp(prefix='store-coordinates-browser-'), FakeClient())
    views._same_day_now = lambda: datetime(2026, 10, 1, 9)
    state = app.extensions['route_optimizer']
    state.crew_days_loader = lambda *args: {}
    state.driver_list_loader = lambda *args: []
    state.same_day_loader = None
    timer = [1000.0]
    current = [make_snapshot()]
    offline = [False]

    def load():
        if offline[0]:
            raise erp.ErpError('synthetic ERP outage')
        return current[0]

    state.snapshots = SnapshotCache(load, clock=lambda: timer[0])
    response = app.test_client().post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert response.status_code == 200, response.get_json()
    before = state.store.load_dispatch('2026-10-01')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    checks = []

    def check(condition, text):
        assert condition, text
        checks.append(text)

    def open_cards(page):
        tabs = page.locator('#dpTabs')
        if tabs.is_visible():
            page.locator('#dpTabs [data-tab="trips"]').click()
        page.locator('#dpTruckCards .dp-thead').first.wait_for(state='visible')
        for head in page.locator('#dpTruckCards .dp-thead').all():
            if head.get_attribute('aria-expanded') != 'true':
                head.click()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for theme, width, height in [('light', 1440, 960), ('dark', 390, 844), ('light', 320, 568)]:
                page = browser.new_page(viewport={'width': width, 'height': height})
                page.add_init_script("(() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();localStorage.setItem('rtTheme', %s);" % json.dumps(theme))
                page.add_init_script("""(() => {
                    const originalNow = Date.now.bind(Date); let offset = 0;
                    Date.now = () => originalNow() + offset;
                    window.advanceCoordinatePoll = () => {
                        offset += 5 * 60 * 1000 + 1000;
                        document.dispatchEvent(new Event('visibilitychange'));
                    };
                })();""")
                full_reads = []
                page.on('request', lambda req: full_reads.append(req.url)
                        if '/api/routes/dispatch?' in req.url else None)
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and not is_ignorable(msg) else None)
                for pattern in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    page.route(pattern, lambda route: route.fulfill(status=200, content_type='image/png', body=TILE_PNG))
                page.goto(f'http://127.0.0.1:{server.server_port}/routes/dispatch?date=2026-10-01')
                page.wait_for_selector('#dpBody', state='visible')
                open_cards(page)
                page.locator('.dp-stop').first.wait_for(state='visible')
                check(not any('ERP հասցե' in t for t in page.locator('.dp-stop-tags').all_text_contents()), f'{theme}/{width}: default ERP source has no chip')
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'{theme}/{width}: no horizontal page overflow')
                if width == 1440:
                    old = current[0]
                    current[0] = replace(old, id='refreshed', data_as_of=old.data_as_of + timedelta(hours=1),
                                         erp_points={**old.erp_points, 1001: (101, (40.181, 44.501))})
                    timer[0] += 601
                    with page.expect_response('**/api/routes/dispatch?date=*') as response:
                        page.evaluate('window.advanceCoordinatePoll()')
                    check(response.value.ok and 'T13:00' in response.value.text(), 'automatic poll reloads a coordinate-only change')
                    check(state.store.load_dispatch('2026-10-01') == before, 'automatic coordinate refresh preserves plan and revision')
                # Open the current store details using the rendered accessible control.
                open_cards(page)
                page.locator('.dp-editbtn:visible').first.click()
                visible_buttons = page.locator('button[aria-label^="Փոխել տեղը՝"]:visible')
                if visible_buttons.count():
                    visible_buttons.first.click()
                    page.locator('#dpGeoDlg').wait_for(state='visible')
                    check('ստացված՝' in page.locator('#dpGeoLead').inner_text(), f'{theme}/{width}: dialog shows source/time')
                    if width == 1440:
                        initial_input = page.locator('#dpGeoCoord').input_value()
                        old = current[0]
                        current[0] = replace(old, id='after-dialog', data_as_of=old.data_as_of + timedelta(hours=1),
                                             erp_points={**old.erp_points, 1001: (101, (40.182, 44.502))})
                        timer[0] += 601
                        read_count = len(full_reads)
                        with page.expect_response('**/api/routes/dispatch/status?date=*'):
                            page.evaluate('window.advanceCoordinatePoll()')
                        page.wait_for_timeout(200)
                        check(page.locator('#dpGeoDlg').is_visible()
                              and page.locator('#dpGeoCoord').input_value() == initial_input
                              and len(full_reads) == read_count,
                              'coordinate polling preserves an open location edit')
                    page.screenshot(path=str(args.output / f'geo-{theme}-{width}.png'))
                    page.locator('#dpGeoCancel').click()
                    if width == 1440:
                        with page.expect_response('**/api/routes/dispatch?date=*') as deferred:
                            page.evaluate('window.advanceCoordinatePoll()')
                        check('T14:00' in deferred.value.text(), 'coordinate change deferred during edit reloads after closing the dialog')
                        open_cards(page)
                page.locator('.dp-stop:visible').first.scroll_into_view_if_needed()
                page.screenshot(path=str(args.output / f'stores-{theme}-{width}.png'))
                check(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'{theme}/{width}: metadata fits after refresh')
                page.close()
            # ERP unavailable: previous point is visibly qualified, rather than silently claiming a fresh read.
            offline[0] = True
            timer[0] += 61
            page = browser.new_page(viewport={'width': 1440, 'height': 960})
            page.add_init_script("(() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();")
            page.goto(f'http://127.0.0.1:{server.server_port}/routes/dispatch?date=2026-10-01')
            page.wait_for_selector('#dpBody', state='visible')
            open_cards(page)
            page.locator('#dpRefresh').click()
            page.get_by_text('ERP-ից թարմացնել չհաջողվեց․ նախորդ տվյալներն են', exact=False).first.wait_for()
            check(True, 'ERP outage shows stale coordinate notice')
            page.reload()
            page.wait_for_selector('#dpBody', state='visible')
            open_cards(page)
            page.get_by_text('ERP-ից թարմացնել չհաջողվեց․ նախորդ տվյալներն են', exact=False).first.wait_for()
            check(True, 'normal page reload retains stale warning without a successful ERP read')
            page.screenshot(path=str(args.output / 'erp-outage.png'))
            offline[0] = False
            timer[0] += 61
            with page.expect_response('**/api/routes/dispatch?*refresh=1'):
                page.locator('#dpRefresh').click()
            page.wait_for_function("() => !document.getElementById('dpTruckCards').textContent.includes('ERP-ից թարմացնել չհաջողվեց')")
            check(state.store.load_dispatch('2026-10-01') == before,
                  'successful ERP recovery clears the warning and preserves the plan')
            browser.close()
        check(not errors, 'no application JavaScript errors: ' + repr(errors))
        (args.output / 'results.json').write_text(json.dumps({'checks': checks, 'errors': errors}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'{len(checks)} browser checks passed')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

"""Браузерная проверка: утверждение отдельно от отправки, предупреждение, день плана в подписях, водители и неизвестный вес
(запуск вручную, Playwright; ERP — поддельная, базы — временные).

A завтра: итог «բոլոր N վարորդները սպասվում են», подписи «Վաղը», водитель «սպասվում է», итог пересборки, окно «×» — «Վաղը»;
B есть условия — окно со списком, «Չեղարկել» ничего не отправляет; условий нет — без окна, approve с send:false;
C утверждён, не отправлен: амбер-таблетка, плашка, шаг 4 с кнопкой «Ուղարկել վարորդներին», снимка водителям нет;
D «Ուղարկել» — снимок есть, таблетка «ուղարկված», плашки нет;
E сегодня — подписи и «եկել է» прежние, другой день — «Այդ օրը»;
F вес 0 — «քաշն անհայտ է» в узкой правой панели (помещается), строка на шаге «Երթեր». Скриншоты — во временной папке.
"""
from __future__ import annotations

import json
import logging
import re
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

PORT = 8792
BASE = f'http://127.0.0.1:{PORT}'
TODAY, DAY, OTHER = '2026-09-30', '2026-10-01', '2026-10-02'
SHOT = Path(tempfile.gettempdir()) / 'dispatch-publish-split.png'
SHOT_WS = Path(tempfile.gettempdir()) / 'dispatch-publish-split-panel.png'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-split-'), FakeClient())
    state = app.extensions['route_optimizer']
    state.store.save_truck_driver('CAR1', TODAY, 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', TODAY, 'Կարեն', 'qa')
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)
    views._yerevan_now = lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    mods = {'no_warn': False, 'zero_kg': False, 'report': False}

    def tweak(route):
        """Ответ дня: без условий предупреждения / нулевой вес первой точки (остальное — как с сервера)."""
        if route.request.method != 'GET':
            return route.continue_()
        resp = route.fetch()
        body = resp.json()
        if mods['no_warn']:
            body.update(orders_still_coming=False, stops_no_coords=[], new_since_build=None, backlog=[])
        if mods['zero_kg'] and body.get('plan'):
            body['plan']['trucks'][0]['trips'][0]['stops'][0]['kg'] = 0
        if mods['report'] and body.get('plan'):
            cid = body['plan']['trucks'][0]['trips'][0]['stops'][0]['customer_id']
            body['last_build'] = {'at': '2026-09-30T18:00:00', 'moved': [{'customer_id': cid, 'from': 'CAR1', 'to': 'CAR2'}],
                                  'km_before': 20.0, 'km_after': 2.8}
        return route.fulfill(response=resp, json=body)

    def wire(page):
        page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
        page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
        page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
        for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
            page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
        page.route(re.compile(r'/api/routes/dispatch\?date='), tweak)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 889}).new_page()
            page.add_init_script("(() => { const m = matchMedia.bind(window); window.matchMedia = q => m(q === '(min-width: 1101px)' ? '(min-width: 99999px)' : q); })();")
            wire(page)

            def data(day=DAY):
                return page.evaluate("(d) => fetch('/api/routes/dispatch?date=' + d).then(r => r.json())", day)

            def rebuild():
                page.evaluate("""(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())
                    .then(d => fetch('/api/routes/dispatch/build', {method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({date: day, trucks: d.trucks.map(t => t.car_code)})}))""", DAY)

            def goto(day):
                page.goto(f'{BASE}/routes/dispatch?date={day}')
                page.wait_for_selector('#dpBody', state='visible', timeout=30000)

            # A — завтра: до сборки
            goto(DAY)
            page.wait_for_function("() => document.getElementById('dpTodo').innerText.includes('վարորդ')", timeout=15000)
            todo = page.inner_text('#dpTodo')
            check('բոլոր 2 վարորդները սպասվում են' in todo, 'A summary: all drivers expected: ' + todo[:140])
            check(page.text_content('th[data-pd="U"]').strip() == 'Վաղը', 'A fleet column reads «Վաղը»')
            check('Վաղը չենք տանում' in page.text_content('#dpExcluded .rt-fold-t'), 'A excluded fold reads «Վաղը չենք տանում»')
            first = page.locator('#dpFleetRows label.dp-pres').first
            check(first.text_content().strip() == 'սպասվում է', 'A attendance reads «սպասվում է»')
            check(first.get_attribute('title') == 'Սեղմեք, եթե վարորդը չի գալու', 'A attendance tooltip for a future day')
            page.evaluate("document.querySelector('#dpFleetRows input.dp-prescb').click()")
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            check(page.inner_text('#dpAbsentTitle').strip() == 'Վարորդը չի գալու', 'A absent dialog title on a future day')
            page.click('#dpAbsentCancel')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=5000)
            rebuild()
            page.reload()
            page.wait_for_selector('.dp-stop .dp-stop-x', state='attached', timeout=30000)
            page.evaluate("() => document.querySelector('.dp-stop .dp-stop-x').click()")
            page.wait_for_function("() => document.getElementById('dpWhyDlg').open", timeout=10000)
            opts = page.inner_text('#dpWhyOpts')
            check('Վաղը չենք տանում' in opts and 'Միայն վաղը' in opts and 'Այսօր' not in opts, 'A why-remove options read «Վաղը»')
            page.keyboard.press('Escape')
            page.wait_for_function("() => !document.getElementById('dpWhyDlg').open", timeout=5000)
            mods['report'] = True                                       # итог пересборки: 1 магазин сменил машину, −17,2 км
            page.reload()
            page.wait_for_selector('#dpApproveBtn', state='attached', timeout=30000)
            page.click('#dpWizardStart')
            page.wait_for_selector('#dpWizard[open]', timeout=5000)
            page.click('[data-guide-step="2"]')
            page.wait_for_selector('#dpGuideWarnings .dp-lastbuild', timeout=5000)
            note = page.locator('#dpGuideWarnings .dp-lastbuild')
            check(note.locator('summary').inner_text().replace('\xa0', ' ') == 'Վերջին կազմումից հետո 1 խանութ փոխեց մեքենան · −17,2 կմ',
                  'A rebuild report in step 3: ' + note.locator('summary').inner_text())
            note.locator('summary').click()
            check(note.locator('li').count() == 1 and '→' in note.locator('li').inner_text() and 'Клиент' in note.locator('li').inner_text(),
                  'A store names on expand: ' + note.locator('li').inner_text())
            page.click('#dpGuideClose')

            # B — предупреждение
            page.click('#dpApproveBtn')
            page.wait_for_selector('#dpWizard[open]', timeout=5000)
            n = len(posts)
            page.click('#dpApproveBtn')
            page.wait_for_selector('#dpConfirmDlg[open]', timeout=5000)
            text = page.inner_text('#dpConfirmText')
            check('Քարտեզի կետ չունի' in text, 'B warning dialog lists the store without a point: ' + text.replace('\n', ' | ')[:160])
            page.click('#dpConfirmCancel')
            page.wait_for_function("() => !document.getElementById('dpConfirmDlg').open", timeout=5000)
            check(not [p for p in posts[n:] if p[0] == 'edit'], 'B cancel posts nothing')
            check(not data().get('approved'), 'B plan stays not approved')
            page.click('#dpGuideClose')
            mods['no_warn'] = True
            mods['report'] = False
            page.reload()
            page.wait_for_selector('#dpApproveBtn', state='attached', timeout=30000)
            page.click('#dpApproveBtn')
            page.wait_for_selector('#dpWizard[open]', timeout=5000)
            n = len(posts)
            page.click('#dpApproveBtn')
            page.wait_for_selector('#dpToast.is-on', timeout=15000)
            check(page.locator('#dpConfirmDlg[open]').count() == 0, 'B no conditions — no dialog')
            edits = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(edits) == 1 and edits[0].get('action') == 'approve' and edits[0].get('send') is False, 'B approve posts send:false: ' + str(edits))
            check('վարորդներին դեռ չի ուղարկվել' in page.inner_text('#dpToast'), 'B toast does not claim sending')

            # C — утверждён, не отправлен
            mods['no_warn'] = False
            check(page.locator('#dpGuideFinalWarnings .dp-lastbuild').count() == 0, 'C no rebuild report once approved')
            d = data()
            check(bool(d.get('approved')) and 'sent' not in d and 'released' not in d, 'C approved, no driver snapshot')
            pill = page.locator('#dpSendState .dp-sendpill')
            check('is-unsent' in pill.get_attribute('class') and 'Հաստատված է · չի ուղարկվել' in pill.text_content(), 'C amber pill: ' + pill.text_content()[:60])
            send = page.locator('#dpGuideSend')
            check(send.is_visible() and 'rt-btn-primary' in send.get_attribute('class') and 'Ուղարկել վարորդներին' in send.inner_text(), 'C step 4: primary send button')
            check('ուղարկեք վարորդներին' in page.inner_text('#dpGuideFinal'), 'C step 4 heading asks to send')
            page.screenshot(path=str(SHOT))
            page.click('#dpGuideClose')
            banner = page.locator('#dpUnsentBanner')
            check(banner.is_visible() and 'Վարորդները դեռ չեն տեսնում այս պլանը' in banner.inner_text(), 'C banner on the working screen')

            # D — отправка
            n = len(posts)
            page.click('#dpUnsentSend')
            page.wait_for_selector('#dpWizard[open]', timeout=5000)
            page.click('#dpGuideSend')
            page.wait_for_function("() => document.getElementById('dpGuideFinal').innerText.includes('Պլանն ուղարկված է')", timeout=15000)
            edits = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(edits) == 1 and edits[0].get('action') == 'send', 'D send posted: ' + str(edits))
            d = data()
            check(bool(d.get('sent')) and d.get('released') is True and bool(d.get('approved')), 'D driver snapshot exists')
            check('is-sent' in pill.get_attribute('class'), 'D pill is «sent»')
            page.click('#dpGuideClose')
            check(banner.is_hidden(), 'D banner gone')

            # E — сегодня и другой день
            goto(TODAY)
            page.wait_for_function("() => document.getElementById('dpFleetRows').querySelector('label.dp-pres')", timeout=15000)
            check(page.text_content('th[data-pd="U"]').strip() == 'Այսօր', 'E today column stays «Այսօր»')
            check('Այսօր չենք տանում' in page.text_content('#dpExcluded .rt-fold-t'), 'E today fold unchanged')
            first = page.locator('#dpFleetRows label.dp-pres').first
            check(first.text_content().strip() == 'եկել է' and first.get_attribute('title') == 'Սեղմեք, եթե վարորդն այսօր չի եկել', 'E today attendance unchanged')
            page.evaluate("document.querySelector('#dpFleetRows input.dp-prescb').click()")
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            check(page.inner_text('#dpAbsentTitle').strip() == 'Վարորդը չի եկել', 'E today absent dialog title unchanged')
            page.click('#dpAbsentCancel')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=5000)
            goto(OTHER)
            page.wait_for_function("() => document.getElementById('dpFleetRows').querySelector('label.dp-pres')", timeout=15000)
            check(page.text_content('th[data-pd="U"]').strip() == 'Այդ օրը', 'E other day reads «Այդ օրը»')
            page.evaluate("document.querySelector('#dpFleetRows input.dp-prescb').click()")
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            page.click('#dpAbsentSave')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=10000)
            page.wait_for_function("() => [...document.querySelectorAll('#dpFleetRows label.dp-pres')].some(l => l.textContent.includes('չի գալու'))", timeout=10000)
            check(True, 'E future-day absent driver reads «չի գալու»')

            # F — неизвестный вес в узкой панели рабочего экрана
            mods['zero_kg'] = True
            ws = browser.new_context(viewport={'width': 1440, 'height': 889}).new_page()
            wire(ws)
            ws.goto(f'{BASE}/routes/dispatch?date={DAY}')
            ws.wait_for_selector('#dpWsRows .dp-wl-row', timeout=30000)
            code = ws.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json()).then(d => d.plan.trucks[0].car_code)")
            ws.click(f'#dpWsRows .dp-wl-row[data-truck="{code}"]')
            ws.wait_for_selector('#dpWsSide:not([hidden]) .dp-kg-unknown', timeout=8000)
            cell = ws.locator('#dpWsSide .dp-kg-unknown').first
            check(cell.inner_text().strip() == 'քաշն անհայտ է' and 'չի հաշվվում' in cell.get_attribute('title'), 'F unknown weight text and title')
            fit = ws.evaluate("""() => { const side = document.getElementById('dpWsSide'), c = side.querySelector('.dp-kg-unknown');
                const s = side.getBoundingClientRect(), r = c.getBoundingClientRect();
                return { inside: r.left >= s.left - 1 && r.right <= s.right + 1, over: side.scrollWidth - side.clientWidth, cellOver: c.scrollWidth - c.clientWidth }; }""")
            check(fit['inside'] and fit['over'] <= 1 and fit['cellOver'] <= 1, f'F fits the narrow panel: {fit}')
            shape = ws.evaluate("""() => { const c = document.querySelector('#dpWsSide .dp-kg-unknown'), row = c.closest('.dp-stop');
                const a = c.getBoundingClientRect(), b = row.querySelector('.dp-stop-eta').getBoundingClientRect();
                const hit = a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
                return { lines: c.getClientRects().length, h: Math.round(a.height), hit }; }""")
            check(shape['lines'] == 1 and shape['h'] <= 22 and not shape['hit'], f'F one line, not over the arrival time: {shape}')
            table = ws.locator('#dpStops .dp-st-c-kg.dp-kg-unknown').first
            check(table.count() == 1 and table.text_content().strip() == 'քաշն անհայտ է', 'F unknown weight in the stops table')
            ws.locator('#dpWsSide').screenshot(path=str(SHOT_WS))
            ws.click('#dpWizardStart')
            ws.wait_for_selector('#dpWizard[open]', timeout=5000)
            ws.click('[data-guide-step="2"]')
            ws.wait_for_function("() => document.getElementById('dpGuideWarnings').innerText.includes('խանութի քաշն անհայտ է')", timeout=5000)
            check(True, 'F step 3 issue line')
            check(not errors, 'no page or console errors: ' + str(errors[:3]))
            browser.close()
    finally:
        server.shutdown()
    print('RESULT', sum(results), '/', len(results), '· screenshots:', SHOT, SHOT_WS)
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

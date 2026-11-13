# -*- coding: utf-8 -*-
"""Браузерная проверка шага «Պատվեր» мастера (запуск вручную, Playwright; ERP — поддельная, базы — временные).

Владелец 12.11.2026: «страница непонятная и некрасивая». Шаг показывает одну фразу «что везём», ниже — что в доставку
не входит (правило менеджеров, сам менеджер везёт), «Նախորդ օրերից» без кнопки и предупреждение «нет точки» с кнопкой.
Старые плитки и напоминания блока «Պատվերներ» в мастере скрыты, его списки открываются кнопками строк. Заголовок без рамки
фокуса, главная кнопка — справа, на телефоне нет горизонтальной прокрутки.
Снимки: <temp>/dispatch-guide-orders{,-dark,-phone}.png
"""
from __future__ import annotations

import logging
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
from test_route_optimizer import _dispatch_setup, _dorder  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8851
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
OLD = date(2026, 9, 28)
SHOT = Path(tempfile.gettempdir()) / 'dispatch-guide-orders.png'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-guide-orders-'), FakeClient())
    # правило «какие машины везут магазин» сохраняется с паролем суперадмина (как в test_route_optimizer)
    app.config['ROUTES_SUPERADMIN_PASSWORD_HASH'] = generate_password_hash('test-vehicle-approval', method='pbkdf2:sha256:1000')
    # менеджер 1 — везём; менеджер 2 — снят правилом настроек; заказ 5 менеджер везёт сам; 999 — без точки; 6, 7 — прошлые дни
    orders = [_dorder(1, 101, 400.0), _dorder(4, 999, 50.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
              _dorder(5, 101, 100.0, van=1), _dorder(6, 104, 70.0, day=OLD), _dorder(7, 101, 55.0, day=OLD)]
    with app.test_request_context():
        class _C:       # _dispatch_setup ждёт test-client
            application = app
            post = staticmethod(lambda url, json=None: app.test_client().post(url, json=json))
        _dispatch_setup(_C, orders)
        r = app.test_client().post('/api/routes/settings', json={'settings': {'dispatch_agents_off': [2]}})
        assert r.status_code == 200, r.get_json()
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)
    views._yerevan_now = lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            def open_page(width, height):
                page = browser.new_context(viewport={'width': width, 'height': height}).new_page()
                page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
                page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
                page.goto(f'{BASE}/routes/dispatch?date={DAY}')
                page.wait_for_selector('#dpWizard[open] #dpGuideSummary:not([hidden])', timeout=30000)
                return page

            page = open_page(1440, 889)
            box = page.locator('#dpGuideSummary')
            txt = lambda loc: loc.inner_text().replace(' ', ' ')  # noqa: E731  числа и слова — через неразрывный пробел
            lead = box.locator('.dp-gsum-lead').inner_text()
            check(lead.startswith('Վաղը կտանենք ') and 'պատվեր' in lead and 'խանութի' in lead, 'lead sentence: ' + lead)
            meta = box.locator('.dp-gsum-meta').inner_text()
            check('Քաշը՝' in meta and 'Գումարը՝' in meta and '֏' in meta, 'weight and sum under the lead: ' + meta.replace('\n', ' | '))
            check(page.locator('#dpGuideProgress').inner_text() == 'Վաղը՝ հինգշաբթի, 1 հոկտեմբերի',
                  'day above the title: ' + page.locator('#dpGuideProgress').inner_text())
            accepted = page.locator('#dpGuideAccepted').inner_text()
            check(accepted.startswith('Ընդունված պատվերներ՝ '), 'accepted orders next to the date: ' + accepted)

            out = box.locator('.dp-gsum-part:not(.is-later)')
            check(out.locator('h3').inner_text() == 'Առաքման մեջ չեն մտնում', 'out list heading')
            agents = out.locator('.dp-gsum-row', has_text='մենեջերի պատվերները')
            check(agents.count() == 1 and 'Կարգավորումների կանոնով' in agents.inner_text(), 'managers row says the rule')
            check(txt(agents.locator('.dp-gsum-v')) == '2 պատվեր · 1,5 տ', 'managers row value: ' + txt(agents.locator('.dp-gsum-v')))
            selfd = out.locator('.dp-gsum-row', has_text='Մենեջերն ինքն է տանում')
            check(selfd.count() == 1 and txt(selfd.locator('.dp-gsum-v')) == '1 պատվեր · 100 կգ' and selfd.locator('button').count() == 0,
                  'self-delivery row, no button')
            later = box.locator('.dp-gsum-part.is-later .dp-gsum-row')
            check(later.count() == 1 and 'Նախորդ օրերից՝ 2 չառաքված պատվեր' in txt(later)
                  and 'Կորոշեք «Երթեր» քայլում' in later.inner_text() and later.locator('button').count() == 0,
                  'previous days: one row, decided on the routes step, no button')
            warn = box.locator('.dp-gsum-warn .dp-gsum-row')
            check(warn.count() == 1 and 'տեղը քարտեզում նշված չէ' in warn.inner_text(), 'no-point warning above the summary')

            hidden = page.evaluate("""() => ['dpOrderStats', 'dpAttn', 'dpOrdersInfo'].map(id => document.getElementById(id))
                .every(el => el.closest('#dpWizard') && el.getClientRects().length === 0)""")
            check(hidden, 'old tiles and reminders are not shown inside the wizard')
            check(page.locator('#dpStep2 > .dp-step-head').is_hidden(), 'panel step head is not shown inside the wizard')

            title = page.evaluate("""() => { const h = document.getElementById('dpGuideTitle'), s = getComputedStyle(h);
                return [document.activeElement === h, s.outlineStyle, s.boxShadow]; }""")
            check(title[0] and title[1] == 'none' and title[2] == 'none', 'focused title has no frame: ' + str(title))
            nxt, foot = page.locator('#dpGuideNext').bounding_box(), page.locator('.dp-guide-footer').bounding_box()
            check(foot['x'] + foot['width'] - (nxt['x'] + nxt['width']) < 40, 'main button sits on the right')
            cur = page.evaluate("""() => { const b = document.querySelector('#dpWizard [data-guide-step="0"]');
                return [b.getAttribute('aria-current'), getComputedStyle(b, '::after').backgroundColor,
                        getComputedStyle(document.querySelector('#dpWizard [data-guide-step="2"]'), '::after').backgroundColor]; }""")
            check(cur[0] == 'step' and cur[1] != cur[2], 'current step segment differs from a later one: ' + str(cur))

            check(page.locator('#dpGuideFootnote').inner_text() == '' and page.locator('#dpGuideFootnote').is_hidden(),
                  'no «go back» note on the first step')
            # перерисовка без изменений (renderGuide зовут часто) не пересоздаёт кнопки: фокус клавиатуры остаётся на месте
            same = page.evaluate("""() => { const b = document.querySelector('#dpGuideSummary [data-guide-open="dpAgents"]'); b.focus();
                document.getElementById('dpGuideOrders').dispatchEvent(new Event('change', {bubbles: true}));
                return document.querySelector('#dpGuideSummary [data-guide-open="dpAgents"]') === b && document.activeElement === b; }""")
            check(same, 'unchanged re-render keeps the buttons and the focus')

            labels = page.evaluate("() => [...document.querySelectorAll('#dpWizard .dp-guide-step-state')].map(s => s.textContent)")
            check(labels[1:] == ['Չի սկսվել', 'Չի սկսվել', 'Չի սկսվել'], 'steps not done are not «Պատրաստ»: ' + str(labels))
            # списки, которые открывают кнопки строк, под сводкой не повторяются — видны только раскрытыми
            check(page.locator('#dpAgents').is_hidden() and page.locator('#dpNoCoords').is_hidden(),
                  'linked lists are not repeated under the summary')

            page.screenshot(path=str(SHOT))
            agents.locator('button').click()
            page.wait_for_function("() => document.getElementById('dpAgents').open", timeout=5000)
            check(page.locator('#dpAgents').is_visible() and page.evaluate(
                "() => document.activeElement === document.querySelector('#dpAgents > summary')"),
                  'managers «Դիտել» opens the managers list and focuses it')
            page.locator('#dpAgents > summary').click()
            page.wait_for_function("() => !document.getElementById('dpAgents').open", timeout=5000)
            back = "() => document.activeElement === document.querySelector('#dpGuideSummary [data-guide-open=dpAgents]')"
            try:      # событие toggle приходит после закрытия — ждём его
                page.wait_for_function(back, timeout=5000)
            except Exception:  # noqa: BLE001  проверка ниже покажет FAIL
                pass
            check(page.locator('#dpAgents').is_hidden() and page.evaluate(back), 'closing the list hides it again and returns focus to «Դիտել»')
            agents.locator('button').click()
            page.wait_for_function("() => document.getElementById('dpAgents').open", timeout=5000)
            warn.locator('button').click()
            page.wait_for_function("() => document.getElementById('dpNoCoords').open", timeout=5000)
            check(True, '«Նշել քարտեզում» opens the stores without a point')

            page.evaluate("() => { document.documentElement.dataset.rtTheme = 'dark'; document.getElementById('dpGuideScroll').scrollTop = 0; }")
            page.screenshot(path=str(SHOT.with_name('dispatch-guide-orders-dark.png')))
            dark = page.evaluate("() => getComputedStyle(document.querySelector('#dpGuideSummary .dp-gsum-card')).backgroundColor")
            check(dark != 'rgb(252, 252, 253)', 'dark theme: summary card is not light: ' + dark)

            phone = open_page(390, 844)
            over = phone.evaluate("() => { const s = document.getElementById('dpGuideScroll'); return s.scrollWidth - s.clientWidth; }")
            check(over <= 0, f'phone: no horizontal scroll in the step ({over}px)')
            check(phone.locator('#dpGuideSummary .dp-gsum-lead').is_visible(), 'phone: lead sentence visible')
            phone.screenshot(path=str(SHOT.with_name('dispatch-guide-orders-phone.png')))

            # день без заказов (правило сняло обоих менеджеров): фразы нет, но видно, почему (строка менеджеров) и что без точки
            phone.evaluate("""() => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({settings: {dispatch_agents_off: [1, 2]}})})""")
            empty = open_page(1440, 889)
            ebox = empty.locator('#dpGuideSummary')
            check(empty.locator('#dpOrdersEmpty').is_visible() and ebox.locator('.dp-gsum-lead').count() == 0,
                  'no orders: empty notice, no lead sentence')
            erow = ebox.locator('.dp-gsum-row', has_text='մենեջերի պատվերները')
            check(erow.count() == 1 and erow.is_visible() and 'Կարգավորումների կանոնով' in erow.inner_text(),
                  'no orders: the managers rule row still explains why')

            # шаги «Մեքենա», «Երթեր», «Ուղարկել» на собранном плане (тот же вид, что у шага «Պատվեր»)
            empty.evaluate("""(day) => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({settings: {dispatch_agents_off: [2]}})})
                .then(() => fetch('/api/routes/dispatch?date=' + day)).then(r => r.json())
                .then(d => fetch('/api/routes/dispatch/build', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: day, trucks: d.trucks.map(t => t.car_code)})}))""", DAY)
            plan = browser.new_context(viewport={'width': 1440, 'height': 889}).new_page()
            plan.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            plan.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                plan.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            plan.goto(f'{BASE}/routes/dispatch?date={DAY}')
            plan.wait_for_selector('#dpWizardStart:not(:disabled)', state='attached', timeout=30000)
            plan.evaluate("() => { if (!document.getElementById('dpWizard').open) document.getElementById('dpWizardStart').click(); }")
            plan.wait_for_selector('#dpWizard[open]', timeout=5000)
            size = lambda: plan.evaluate("() => { const r = document.getElementById('dpWizard').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }")  # noqa: E731,E501
            plan.click('#dpWizard [data-guide-step="0"]')
            first = size()
            plan.click('#dpWizard [data-guide-step="1"]')
            scope = plan.evaluate("""() => { const s = document.getElementById('dpGuideFleetScope');
                return [s.parentElement === document.querySelector('#dpStep1 > .dp-step-head') && s.parentElement.firstElementChild === s, s.textContent]; }""")
            check(scope[0] and scope[1].startswith('Պլանում՝'), 'step 2: plan line sits in the trucks table header: ' + scope[1])
            pill = plan.evaluate("""() => { const p = document.querySelector('#dpFleetRows tr[data-drv="none"] .dp-pill.is-none');
                return p ? [getComputedStyle(p).color, getComputedStyle(document.querySelector('#dpGuideTitle')).color] : null; }""")
            check(pill and pill[0] == 'rgb(163, 95, 0)', 'step 2: truck without a driver is marked in its row: ' + str(pill))
            plan.click('#dpWizard [data-guide-step="2"]')
            trips = plan.locator('#dpWizard .dp-guide-trips')
            check(trips.is_visible() and trips.locator('h3').inner_text() == 'Երթերն ըստ մեքենաների'
                  and trips.locator('.dp-guide-route').count() >= 1, 'step 3: routes by truck shown first, not folded')
            check(plan.evaluate("() => !document.getElementById('dpGuideSwapFold').open && !document.querySelector('#dpWizard .dp-vehicle-route-details')"),
                  'step 3: truck swap is folded, the old routes fold is gone')
            stats = plan.evaluate("() => getComputedStyle(document.getElementById('dpGuideStats')).paddingLeft")
            check(stats == '22px', 'step 3: totals are a padded card: ' + stats)
            adv = plan.locator('#dpGuideWarnings .dp-guide-warning.is-advisory')
            shown = plan.evaluate("() => [...document.querySelectorAll('#dpGuideWarnings .dp-guide-warning.is-advisory small')]"
                                  ".filter(s => s.getClientRects().length).length")
            check(adv.count() >= 1 and shown == 0, 'step 3: no repeated «Ստուգեք» label on advisory rows')
            plan.click('#dpWizard [data-guide-step="3"]')
            check(size() == first, f'step 4: same window size as step 1 ({size()} vs {first})')
            plan.click('#dpGuideClose')
            plan.wait_for_selector('#dpWizard:not([open])', state='attached', timeout=5000)
            back_home = ("() => !!document.getElementById('dpGuideFleetScope').closest('[data-guide-panel=\"1\"]')"
                         " && !document.getElementById('dpStep1').contains(document.getElementById('dpGuideFleetScope'))")
            try:      # узлы возвращаются в обработчике события close — оно приходит после снятия open
                plan.wait_for_function(back_home, timeout=5000)
            except Exception:  # noqa: BLE001  проверка ниже покажет FAIL
                pass
            check(plan.evaluate(back_home), 'closing the wizard puts the plan line back; the side panel trucks block stays clean')

            # 12.11.2026 на сайте: правило менеджеров сменили между сборками (48 → 24 магазина), а магазин центра остался без
            # машины с подсказкой «1-ին քայլ», хотя в мастере машины — шаг «Մեքենա». Здесь: все менеджеры → сборка; затем
            # машина центра — CAR1 (въезжает в центр; магазин 102 в малом центре, своего правила нет), пересборка без CAR1
            flat = lambda t: ' '.join(t.split())  # noqa: E731  неразрывные пробелы и переводы строк — в один пробел
            plan.evaluate("""(day) => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({settings: {dispatch_agents_off: []}})})
                .then(() => fetch('/api/routes/dispatch?date=' + day)).then(r => r.json())
                .then(d => fetch('/api/routes/dispatch/build', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({date: day, trucks: d.trucks.map(t => t.car_code), rev: d.rev})}))
                .then(() => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Superadmin-Password': 'test-vehicle-approval'},
                    body: JSON.stringify({settings: {dispatch_center_role: {truck: 'CAR1', from: '2026-09-01'}}})}))""", DAY)
            before = plan.evaluate("(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json()).then(d => d.plan.summary.stops)", DAY)
            builds = []
            plan.on('request', lambda r: builds.append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/api/routes/dispatch/build') else None)
            plan.reload()
            plan.wait_for_selector('#dpWizardStart:not(:disabled)', state='attached', timeout=30000)
            plan.evaluate("() => { if (!document.getElementById('dpWizard').open) document.getElementById('dpWizardStart').click(); }")
            plan.wait_for_selector('#dpWizard[open]', timeout=5000)
            plan.click('#dpWizard [data-guide-step="1"]')
            car1 = plan.locator('#dpFleetRows input.dp-tkpick[value="CAR1"]')
            if car1.is_checked():
                car1.uncheck()
            plan.click('#dpBuild')
            plan.wait_for_function("() => document.getElementById('dpWizard').dataset.step === '2' && !document.getElementById('dpBuild').disabled",
                                   timeout=60000)
            drop = plan.locator('#dpGuideWarnings .dp-guide-warning', has_text='Նախորդ կազմման համեմատ')
            check(drop.count() == 1 and f'{before} →' in flat(drop.inner_text()),
                  f'after a rebuild with fewer stores: «{before} → …» heads-up on the routes step: ' + (flat(drop.inner_text()) if drop.count() else 'none'))
            plan.screenshot(path=str(SHOT.with_name('dispatch-guide-orders-drop.png')))
            plan.click('#dpGuideWarnings [data-guide-fix="2"]')
            card = plan.locator('#dpGuideIssuesList .dp-unassigned', has_text='չկա խանութը սպասարկող մեքենա')
            card.wait_for(state='visible', timeout=5000)
            plan.screenshot(path=str(SHOT.with_name('dispatch-guide-orders-novehicle.png')))
            ctext = flat(card.inner_text())
            access = plan.evaluate("(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())"
                                   ".then(d => (d.plan.unassigned.find(s => s.customer_id === 102) || {}).access)", DAY)
            check(access == {'mode': 'allow', 'trucks': ['CAR1']}, 'server sends the applied rule (centre truck) on the store: ' + str(access))
            check('Թույլատրված մեքենան՝ CAR1' in ctext and 'մեքենաների ցուցակում' in ctext,
                  'no-vehicle card names the allowed truck and the trucks list: ' + ctext[:160])
            check('1-ին քայլ' not in plan.locator('#dpWizard').inner_text(), 'no «1-ին քայլ» wording inside the wizard')
            add = card.locator('button', has_text='Ավելացնել և վերակազմել')
            check(add.count() == 1, 'no-vehicle card has «Ավելացնել և վերակազմել»')
            n = len(builds)
            add.click()
            plan.wait_for_function("(n) => performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/routes/dispatch/build')).length > n",
                                   arg=n, timeout=60000)
            plan.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=60000)
            sent = builds[n] if len(builds) > n else {}
            placed = plan.evaluate("(day) => fetch('/api/routes/dispatch?date=' + day).then(r => r.json())"
                                   ".then(d => !(d.plan.unassigned || []).some(s => s.customer_id === 102))", DAY)
            check('CAR1' in (sent or {}).get('trucks', []) and placed, f'one click adds CAR1 and rebuilds; store 102 is in a trip: {sent} {placed}')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'no page or console errors: ' + str(errors))
    print(f'RESULT {sum(results)} / {len(results)} screenshot {SHOT}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

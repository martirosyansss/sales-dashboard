# -*- coding: utf-8 -*-
"""Новые заказы дня (ответ владельца №72) на странице «Развоз» (/routes/dispatch) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_same_day_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py; «сегодня» — 2026-10-01 08:00 по Еревану; новые заказы 01.10: 103 (точка
GPS снимка), 999 (без точки), 104 менеджера A002. Порт 8768 на 127.0.0.1.

A «Կազմել երթերը» → плашка «Այսօր եկել է 3 նոր պատվեր» с кнопкой;
B кнопка → диалог: три строки, у 999 галочка недоступна, 103 и 104 отмечены;
C снять 104 → «Ցույց տալ տարբերակները» → варианты, первый — «Ամենաէժանը»; «Ընտրել» → правка same_day с его key и
  rev, диалог закрыт, 103 в рейсе плана с плашкой «Այսօրվա նոր պատվեր»;
D плашка: остались 2 новых, «Արդեն տանում ենք այսօր՝ 1 պատվեր»;
E диалог → «Թողնել վաղվան» (отмечен 104; 999 без точки — тоже) → запроса нет, плашка «2 նոր պատվեր թողնված է վաղվան»;
F в ERP пришёл новый заказ (105) → автообновление (status) без перезагрузки страницы: плашка «Այսօր եկել է 1 նոր պատվեր»;
G «Թողնել վաղվան» у взятого 103 → правка same_day_drop, 103 ушёл из рейсов;
I 10:00 (первые рейсы грузятся), пришли заказ 102 (его рейс уже уехал) и заказ 103 с накладной на сегодня: плашка
  называет накладную, в диалоге — группа «Հաշիվ-ապրանքագիրն արդեն գրված է — գնում է այսօր» (отмечен); отметить 102 и
  103 → варианты: 102 снят и помечен «մնում է վաղվան — երթն արդեն մեկնել է», «Ընտրել» шлёт только то, что можно взять;
  машина не из шага 1 не выделяется «Ամենաէժանը»; «Վերակազմել երթերը» после начала дня — предупреждение (confirm);
H телефон 390×860: с открытым диалогом нет горизонтальной прокрутки;
J без данных о новых заказах (загрузчика нет) — плашки нет, но «Վերակազմել երթերը» в 10:00 всё равно спрашивает;
K «Հաստատել օրվա պլանը» (№73) → правка approve, отметка «Պլանը հաստատված է · ժ. …», все рейсы закреплены, «Վերակազմել»
  недоступна с пояснением, «Ջնջել երթերը» скрыта; «Չեղարկել հաստատումը» (confirm) → unapprove, пересборка снова доступна.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import json
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

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_optimizer import _dorder, _isn  # noqa: E402

PORT = 8768
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
D = date(2026, 10, 1)
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-same-day-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-same-day-'), FakeClient())
    state = app.extensions['route_optimizer']
    old = state.dispatch_loader(None, None, None)
    new = [_dorder(10, 103, 250.0, day=D, rev=7000.0), _dorder(11, 999, 40.0, day=D),
           _dorder(12, 104, 90.0, day=D, agent=2, rev=3000.0)]
    state.dispatch_loader = lambda since, until, day: replace(
        old, orders=tuple(o for o in old.orders if since <= o.order_date < until))
    state.same_day_loader = lambda day: dp.SameDayData(
        tuple(o for o in new if o.order_date == day), {_isn(10): datetime(2026, 10, 1, 7, 41)},
        {103: ('C103', 'Клиент 103'), 105: ('C105', 'Клиент 105')}, {}, datetime(2026, 10, 1, 8, 0))
    views._yerevan_now = lambda: datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
    views._clock = lambda: datetime(2026, 10, 1, 8, 0)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")   # №82: прежний вид «список»
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            confirms = []
            page.on('dialog', lambda d: (confirms.append(d.message), d.accept()))
            page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                    if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            data = lambda: page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")  # noqa: E731
            banner_box = page.locator('#dpSameDay')
            banner = type('Banner', (), {   # текст плашки без неразрывных пробелов (страница ставит их между числом и словом)
                'is_visible': lambda self: banner_box.is_visible(),
                'inner_text': lambda self: banner_box.inner_text().replace(' ', ' ')})()
            dlg = page.locator('#dpSameDayDlg')

            def plan_cids():
                d = data()
                return {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.locator('#dpBuild').click()
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            check(banner.is_visible() and 'Այսօր եկել է 3 նոր պատվեր' in banner.inner_text(), 'A banner: 3 new orders: ' + banner.inner_text())
            check(page.locator('#dpSdOpen').is_visible(), 'A banner button visible')
            page.screenshot(path=str(SHOTS / 'a-banner.png'), full_page=False)

            # B
            page.locator('#dpSdOpen').click()
            page.wait_for_selector('#dpSameDayDlg[open]', timeout=5000)
            rows = dlg.locator('.dp-sd-row')
            boxes = dlg.locator('.dp-sd-row input[type="checkbox"]')
            check(rows.count() == 3, f'B three rows ({rows.count()})')
            states = [(boxes.nth(i).is_checked(), boxes.nth(i).is_disabled()) for i in range(boxes.count())]
            row_999 = dlg.locator('.dp-sd-row', has_text='C999')
            check(row_999.locator('input').is_disabled() and 'Տեղը քարտեզում չկա' in row_999.inner_text(),
                  'B 999 without point: disabled, badge')
            check(sum(1 for c, _ in states if c) == 2, 'B 103 and 104 checked: ' + str(states))
            check('ընդունվել է ժամը 07:41' in dlg.locator('.dp-sd-row', has_text='Клиент 103').inner_text(), 'B entry time shown')

            # C
            dlg.locator('.dp-sd-row', has_text='C104').locator('input').uncheck()
            n = len(posts)
            page.locator('#dpSdPropose').click()
            page.wait_for_selector('#dpSameDayDlg .dp-sd-opt', timeout=30000)
            opts = dlg.locator('.dp-sd-opt')
            asked = [p[1] for p in posts[n:] if p[0] == 'same-day']
            check(len(asked) == 1 and asked[0].get('orders') == [_isn(10)], 'C options asked for 103 only: ' + str(asked))
            check(opts.count() >= 1 and 'Ամենաէժանը' in opts.first.inner_text(), f'C options listed ({opts.count()}), best first')
            check('+' in opts.first.locator('.dp-sd-cost').inner_text() and 'դրամ' in opts.first.inner_text(), 'C cost shown')
            page.screenshot(path=str(SHOTS / 'c-options.png'), full_page=False)
            rev = data()['rev']
            n = len(posts)
            opts.first.locator('button').click()
            page.wait_for_function("() => !document.getElementById('dpSameDayDlg').open", timeout=30000)
            took = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(took) == 1 and took[0].get('action') == 'same_day' and took[0].get('rev') == rev
                  and took[0].get('orders') == [_isn(10)] and isinstance(took[0].get('option'), str),
                  'C edit same_day with key and rev: ' + str(took))
            check(103 in plan_cids(), 'C 103 in plan')
            page.wait_for_selector('#dpTruckCards .rt-badge:has-text("Այսօրվա նոր պատվեր")', state='attached', timeout=10000)
            check(page.locator('#dpTruckCards .rt-badge', has_text='Այսօրվա նոր պատվեր').count() >= 1, 'C stop badge')

            # D
            text = banner_box.text_content().replace(' ', ' ')   # №81: пояснения — под свёрнутым «Մանրամասն»
            check('Այսօր եկել է 2 նոր պատվեր' in text and 'Արդեն տանում ենք այսօր՝ 1 պատվեր' in text, 'D banner: 2 new, 1 taken: ' + text)

            # E
            page.locator('#dpSdOpen').click()
            page.wait_for_selector('#dpSameDayDlg[open]', timeout=5000)
            check(dlg.locator('.dp-sd-row', has_text='Տանում է՝').count() == 1, 'E taken row listed with truck')
            n = len(posts)
            page.locator('#dpSdLaterBtn').click()
            page.wait_for_function("() => !document.getElementById('dpSameDayDlg').open", timeout=5000)
            check(len(posts) == n, 'E «Թողնել վաղվան» sends nothing')
            check('2 նոր պատվեր թողնված է վաղվան' in banner.inner_text(), 'E banner: left for tomorrow: ' + banner.inner_text())

            # F
            new.append(_dorder(13, 105, 60.0, day=D))
            state.same_day_cache.clear()
            page.evaluate("() => { const real = Date.now; Date.now = () => real() + 6 * 60 * 1000;"
                          " document.dispatchEvent(new Event('visibilitychange')); }")
            page.wait_for_function("() => document.getElementById('dpSameDay').innerText.replace(/\u00a0/g, ' ')"
                                   ".includes('Այսօր եկել է 1 նոր պատվեր')",
                                   timeout=15000)
            check(True, 'F poll shows the newly arrived order without reload')

            # G
            page.locator('#dpSdOpen').click()
            page.wait_for_selector('#dpSameDayDlg[open]', timeout=5000)
            n = len(posts)
            dlg.locator('.dp-sd-row', has_text='Տանում է՝').locator('button').click()
            page.wait_for_function("() => document.querySelectorAll('#dpSameDayDlg .dp-sd-row').length === 4"
                                   " && !document.querySelector('#dpSameDayDlg .dp-sd-row button')", timeout=15000)
            drops = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(drops) == 1 and drops[0].get('action') == 'same_day_drop' and drops[0].get('orders') == [_isn(10)],
                  'G edit same_day_drop: ' + str(drops))
            check(103 not in plan_cids(), 'G 103 left the plan')

            # I
            page.keyboard.press('Escape')
            later = datetime(2026, 10, 1, 10, 0, tzinfo=ac.YEREVAN)
            views._yerevan_now = lambda: later
            views._clock = lambda: later.replace(tzinfo=None)
            new.extend([_dorder(14, 102, 50.0, day=D), _dorder(16, 103, 20.0, day=D, shipped=D),
                        _dorder(17, 102, 30.0, day=D, shipped=D)])
            state.same_day_cache.clear()
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            check('հաշիվ-ապրանքագիրն արդեն գրված է' in banner.inner_text(), 'I banner names the invoiced order: ' + banner.inner_text())
            page.locator('#dpSdOpen').click()
            page.wait_for_selector('#dpSameDayDlg[open]', timeout=5000)
            check('Հաշիվ-ապրանքագիրն արդեն գրված է — գնում է այսօր' in dlg.inner_text(), 'I invoiced group heading')
            inv_row = dlg.locator('ul[aria-label="Հաշիվ-ապրանքագիրն արդեն գրված է"] .dp-sd-row', has_text='C103')
            out_row = dlg.locator('ul[aria-label="Հաշիվ-ապրանքագիրն արդեն գրված է"] .dp-sd-row', has_text='C102')
            check(out_row.count() == 1 and 'պլանից դուրս' in out_row.inner_text() and out_row.locator('input').is_disabled()
                  and 'մնում է վաղվան' not in out_row.inner_text(), 'I invoiced on a departed trip: «outside the plan», no checkbox')
            check(inv_row.locator('input').is_checked(), 'I invoiced order checked by default')
            row_102 = dlg.locator('ul[aria-label="Նոր պատվերներ"] .dp-sd-row', has_text='C102')
            check('մնում է վաղվան — երթն արդեն մեկնել է' in row_102.inner_text().replace('\u00a0', ' ')
                  and not row_102.locator('input').is_checked() and row_102.locator('input').is_disabled(),
                  'I 102 on a departed trip: unchecked, disabled and marked')
            box = dlg.locator('ul[aria-label="Նոր պատվերներ"] .dp-sd-row', has_text='C103').locator('input')
            if not box.is_checked():
                box.check()
            n = len(posts)
            page.locator('#dpSdPropose').click()
            page.wait_for_selector('#dpSameDayDlg .dp-sd-opt', timeout=30000)
            check(inv_row.count() == 1 and inv_row.locator('input').is_checked(), 'I invoiced row of store 103 checked')
            best = dlg.locator('.dp-sd-opt.is-best')
            check(all('այսօր նշված չէ' not in best.nth(i).inner_text() for i in range(best.count())),
                  'I unmarked truck never highlighted as cheapest')
            dlg.locator('.dp-sd-opt').first.locator('button').click()
            page.wait_for_function("() => !document.getElementById('dpSameDayDlg').open", timeout=30000)
            took = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(took) == 1 and sorted(took[0].get('orders', [])) == sorted([_isn(10), _isn(16)]),
                  'I take sent only takeable orders: ' + str(took))
            d = data()
            rows = {o['isn']: o for o in d['same_day']['orders']}
            check(rows[_isn(16)]['taken'] and not rows[_isn(14)]['taken'], 'I invoiced taken, 102 stays for tomorrow')
            confirms.clear()
            n = len(posts)
            page.locator('#dpBuild').click()
            page.wait_for_function("() => document.getElementById('dpBuildText').textContent === 'Վերակազմել երթերը'", timeout=30000)
            check(any('արդեն բեռնվում են կամ ճանապարհին են' in m for m in confirms) and any(p[0] == 'build' for p in posts[n:]),
                  'I rebuild after day start asks for confirmation: ' + str(confirms))
            page.screenshot(path=str(SHOTS / 'i-after.png'), full_page=False)

            # K утверждение плана дня (№73)
            check(page.locator('#dpApproveBtn').is_visible(), 'K «Հաստատել օրվա պլանը» visible')
            n = len(posts)
            page.locator('#dpApproveBtn').click()
            page.wait_for_selector('#dpApprovedBadge', timeout=15000)
            sent = [p[1].get('action') for p in posts[n:] if p[0] == 'edit']
            badge = page.locator('#dpApprovedBadge').inner_text()
            check(sent == ['approve'] and 'Պլանը հաստատված է · ժ. ' in badge, 'K approve sent, badge: ' + badge)
            d = data()
            check(all(tr['pinned'] for t in d['plan']['trucks'] for tr in t['trips']), 'K all trips pinned')
            check(page.locator('#dpBuild').is_disabled() and 'նախ չեղարկեք հաստատումը' in page.locator('#dpBuildNote').inner_text()
                  and not page.locator('#dpReset').is_visible(), 'K rebuild disabled and explained, reset hidden')
            page.screenshot(path=str(SHOTS / 'k-approved.png'), full_page=False)
            confirms.clear()
            n = len(posts)
            page.locator('#dpUnapprove').click()
            page.wait_for_selector('#dpApproveBtn', timeout=15000)
            sent = [p[1].get('action') for p in posts[n:] if p[0] == 'edit']
            check(sent == ['unapprove'] and any('Չեղարկե՞լ' in m for m in confirms) and not page.locator('#dpBuild').is_disabled(),
                  'K unapprove with confirmation, rebuild enabled again')
            page.locator('#dpSdOpen').click()
            page.wait_for_selector('#dpSameDayDlg[open]', timeout=5000)

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            dlg_over = page.evaluate("() => { const d = document.getElementById('dpSameDayDlg'); return d.scrollWidth - d.clientWidth; }")
            check(over <= 0 and dlg_over <= 0, f'H phone: no horizontal scroll (page {over}px, dialog {dlg_over}px)')
            page.screenshot(path=str(SHOTS / 'h-phone.png'), full_page=False)

            # J без данных о новых заказах (ERP не ответила) — подтверждение пересборки всё равно спрашивается по дню и часам
            page.keyboard.press('Escape')
            page.set_viewport_size({'width': 1440, 'height': 950})
            state.same_day_loader = None
            state.same_day_cache.clear()
            page.clock.set_system_time(datetime(2026, 10, 1, 10, 0))
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            check(not page.locator('#dpSameDay').is_visible(), 'J no same-day data: no banner')
            confirms.clear()
            page.locator('#dpBuild').click()
            page.wait_for_function("() => document.getElementById('dpBuildText').textContent === 'Վերակազմել երթերը'", timeout=30000)
            check(any('արդեն բեռնվում են կամ ճանապարհին են' in m for m in confirms), 'J rebuild confirmation without same-day data')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('FAIL ' + e)
    ok = all(results) and not errors
    print(f'\n{sum(results)}/{len(results)} checks passed, page errors: {len(errors)}; screenshots: {SHOTS}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

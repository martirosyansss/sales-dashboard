# -*- coding: utf-8 -*-
"""Проверка «Վարորդներ» (ответ владельца №77: водителей меньше, чем машин) на странице «Развоз» в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_crew_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium и интернет — Leaflet и Excel-библиотека с CDN).
Приложение — то же, что в tests/routes_dispatch_browser_check.py (настоящие шаблон, статика и blueprint, поддельная ERP,
синтетический день, временная база); водители: CAR1 — Արամ, CAR2 — Կարեն. Порт 8771 на 127.0.0.1.

A в шаге 1 — «Վարորդներ»: две отмеченные плитки (имя и машина), «եկել է՝ 2 / 2»; после сборки в карточке CAR1 —
  «վարորդ՝ Արամ»;
B сняли отметку Արամ → диалог «Վարորդը չի եկել», по умолчанию «Միայն …ն»; «Չեղարկել» — отметка вернулась, ничего не
  записано;
C снова сняли → «Մինչև» и дата → сохранено: плитка «չի աշխատում մինչև …», «եկել է՝ 1 / 2», подсказка «Վարորդները փոխվել են»
  с кнопкой пересборки;
D пересборка: магазины только в центре, куда въезжает лишь CAR1, — Կարեն ведёт CAR1 «(փոխարինում)», CAR2 — серая
  карточка с причиной, в шаге 1 у CAR2 «վարորդը նստել է այլ մեքենա», в подсказке «Առանց վարորդի…»;
E Բեռնագիր CAR1 — «Վարորդ՝ Կարեն (փոխարինում)»;
F Արամ снова отмечен → подсказка пересобрать; пересборка — серой карточки нет, CAR1 снова с Արամ;
H телефон 390×860: нет горизонтальной прокрутки;
ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов и road-lines).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import views  # noqa: E402
from route_optimizer import waybill as wb  # noqa: E402

PORT = 8771
BASE = f'http://127.0.0.1:{PORT}'
DAY = base.DAY
UNTIL = '2026-10-03'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = base.build_app(tempfile.mkdtemp(prefix='crew-check-'), base.FakeClient())
    state = app.extensions['route_optimizer']
    state.waybill_loader = lambda isns: wb.Lines({}, frozenset(), {})
    state.driver_list_loader = lambda since, until: []
    state.store.save_truck_driver('CAR1', DAY, 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', DAY, 'Կարեն', 'qa')
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)     # «сейчас» — накануне DAY
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1440, 'height': 950})
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text)
                    if m.type == 'error' and not base.is_ignorable(m) else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            tiles = page.locator('#dpCrewList .dp-driver')

            def tile(name):
                return tiles.filter(has_text=name)

            def open_step1():               # после сборки шаг 1 свёрнут; раскрытый — остаётся раскрытым
                if page.get_attribute('#dpStep1Tog', 'aria-expanded') == 'false':
                    page.click('#dpStep1Tog')

            # A
            check(page.is_visible('#dpCrew') and tiles.count() == 2 and tile('Արամ').locator('input').is_checked()
                  and tile('Կարեն').locator('input').is_checked() and 'CAR1' in tile('Արամ').inner_text()
                  and page.inner_text('#dpCrewCount') == 'եկել է՝ 2 / 2', 'A drivers of the day listed, all checked, 2 / 2')
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.click('#dpBuild')
            page.wait_for_selector('#dpTruckCards .dp-tcard', timeout=60000)
            car1 = page.locator('#dpTruckCards .dp-tcard[data-truck="CAR1"]')
            check('վարորդ՝ Արամ' in car1.locator('.dp-tdriver').inner_text(), 'A card CAR1: driver Արամ')

            # B
            open_step1()
            tile('Արամ').locator('input').uncheck()
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            check(page.is_checked('#dpAbsentOne') and page.inner_text('#dpAbsentOneT').startswith('Միայն')
                  and 'Արամ' in page.inner_text('#dpAbsentLead'), 'B dialog: «only this day» by default, driver named')
            page.click('#dpAbsentCancel')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=5000)
            server_crew = page.request.get(f'{BASE}/api/routes/dispatch', params={'date': DAY}).json()['crew']
            check(tile('Արամ').locator('input').is_checked() and not server_crew['drivers'][0]['absent'],
                  'B cancel: tick back, nothing saved')

            # C
            tile('Արամ').locator('input').uncheck()
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            page.fill('#dpAbsentUntil', UNTIL)
            check(page.is_checked('#dpAbsentLong'), 'C typing a date picks «until»')
            page.click('#dpAbsentSave')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=10000)
            page.wait_for_function("() => /Վարորդները փոխվել են/.test(document.getElementById('dpTodo').textContent)", timeout=10000)
            check('չի աշխատում մինչև 03.10.2026' in tile('Արամ').inner_text() and not tile('Արամ').locator('input').is_checked()
                  and page.inner_text('#dpCrewCount') == 'եկել է՝ 1 / 2', 'C saved until 03.10: tile text, 1 / 2')
            got = page.request.get(f'{BASE}/api/routes/dispatch', params={'date': '2026-10-02'}).json()['crew']['drivers'][0]
            check(got['absent'] and got['until'] == UNTIL, 'C the next day he is absent too')

            # D
            page.locator('#dpTodo button', has_text='Վերակազմել երթերը').click()
            page.wait_for_function("() => document.querySelector('#dpTruckCards .dp-tcard.is-unmanned')", timeout=60000)
            head = car1.locator('.dp-tdriver').inner_text()
            idle = page.locator('#dpTruckCards .dp-tcard.is-unmanned')
            check('վարորդ՝ Կարեն (փոխարինում)' in head, f'D CAR1 driven by Կարեն as a substitute: {head!r}')
            check(idle.count() == 1 and idle.first.get_attribute('data-truck') == 'CAR2'
                  and 'Կարեն' in idle.inner_text() and 'նստել է' in idle.inner_text(), f'D CAR2 greyed with reason: {idle.inner_text()!r}')
            open_step1()
            step = page.locator('#dpTrucks .dp-truck', has_text='CAR2').inner_text()
            todo = page.inner_text('#dpTodo')
            check('վարորդը նստել է այլ մեքենա' in step and 'Առանց վարորդի այսօր դուրս չեն գալիս՝ FORD · CAR2' in todo
                  and 'Վարորդները փոխվել են' not in todo, 'D step 1 and the hint name CAR2')

            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'crew-check-desktop.png'), full_page=True)

            # E
            with ctx.expect_page(timeout=15000) as popup:
                car1.locator('.dp-wbbtn').nth(1).click()
            sheet = popup.value
            sheet.wait_for_function("() => document.querySelectorAll('.sheet').length > 0", timeout=15000)
            check('Վարորդ՝ Կարեն (փոխարինում)' in sheet.inner_text('body'), 'E waybill: actual driver marked «փոխարինում»')
            sheet.close()

            # F
            open_step1()
            tile('Արամ').locator('input').check()
            page.wait_for_function("() => /Վարորդները փոխվել են/.test(document.getElementById('dpTodo').textContent)", timeout=10000)
            page.locator('#dpTodo button', has_text='Վերակազմել երթերը').click()
            page.wait_for_function("() => !document.querySelector('#dpTruckCards .dp-tcard.is-unmanned')"
                                   " && /Արամ/.test((document.querySelector('#dpTruckCards .dp-tcard[data-truck=\"CAR1\"] .dp-tdriver') || {}).textContent || '')",
                                   timeout=60000)
            check(tile('Արամ').locator('input').is_checked() and 'Վարորդները փոխվել են' not in page.inner_text('#dpTodo'),
                  'F back: no greyed card, CAR1 with Արամ again')

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(500)
            sw = page.evaluate('() => document.documentElement.scrollWidth')
            check(sw <= 390, f'H phone: scrollWidth={sw}')
            page.screenshot(path=str(Path(tempfile.gettempdir()) / 'crew-check-phone.png'), full_page=False)

            check(not errors, 'no pageerror / console errors' + ('' if not errors else ': ' + ' | '.join(errors[:5])))
            browser.close()
    finally:
        server.shutdown()
    print('ALL OK' if all(results) else 'SOME FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

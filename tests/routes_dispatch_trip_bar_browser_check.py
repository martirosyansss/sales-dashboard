# -*- coding: utf-8 -*-
"""Рабочий экран «Развоз»: шапка рейса выбранной машины — одной полосой внизу панели машины, у нижнего края при любой
прокрутке; точки прокручиваются над ней (владелец №89, 07.10). Настоящий браузер.

Запуск из корня проекта:  python tests/routes_dispatch_trip_bar_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py; порт 8775 на 127.0.0.1.

A выбрали машину (не рейс): панель вверху, а полоса «Երթ 1» уже целиком у нижнего края — время, загрузка, км, кнопки
  одним рядом; в списке над точками рейса — только «Երթ N · время», без кнопок; полоса не выше 45 % панели;
B строка магазина над полосой видна, полоса её не закрывает; Tab по «×» — кнопка не под полосой;
C «Փոփոխել» в полосе: рейс в правке, полоса та же и внизу, фокус на «Պատրաստ է»; «Պատրաստ է» возвращает как было;
D ноутбук 1366×768: полоса целиком внизу и строка магазина над ней;
M день с двумя рейсами машины: прокрутили ко второму — в полосе «Երթ 2»; обратно — «Երթ 1»; рейс 2 на шкале — «Երթ 2»;
E обычный вид (не рабочий экран): полосы нет, шапка рейса с кнопками — над точками, как раньше.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from test_route_optimizer import _dorder  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8775
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
DAY2 = '2026-10-02'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-trip-bar-check'

RECT = "(e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"
CARD = '#dpWsSide .dp-tcard.is-focus'
BAR = '#dpWsTripBar'
# прокрутить только панель (не страницу), чтобы элемент встал на off px ниже верха панели
TO_TOP = "(e, off) => { const p = document.getElementById('dpWsSide'); p.scrollTop += e.getBoundingClientRect().top - p.getBoundingClientRect().top - off; }"


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-trip-bar-'), FakeClient())
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)     # «сейчас» — накануне DAY: день не прошедший, правки доступны
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1600, 'height': 950}).new_page()
            page.add_init_script("try { localStorage.setItem('dpLayout', 'ws'); } catch (e) {}")
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            rect = lambda sel: page.locator(sel).first.evaluate(RECT)  # noqa: E731
            side_loc = page.locator('#dpWsSide')
            bar_trip = lambda: page.locator(BAR).get_attribute('data-trip')  # noqa: E731

            def build(day, uncheck=()):
                page.goto(f'{BASE}/routes/dispatch?date={day}')
                page.wait_for_selector('#rtDispatch', timeout=30000)
                page.wait_for_function("() => document.getElementById('dpBuild') && !document.getElementById('dpBuild').disabled", timeout=15000)
                for code in uncheck:
                    page.evaluate("(c) => { const b = document.querySelector('#dpTrucks input[type=\"checkbox\"][value=\"' + c + '\"]');"
                                  " if (b && b.checked) b.click(); }", code)
                page.evaluate("document.getElementById('dpBuild').click()")
                page.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', timeout=30000)
                page.wait_for_timeout(800)
                if page.locator('#dpDrawer').is_visible():
                    page.click('#dpDrawerClose')

            build(DAY)
            # A — машина целиком (не рейс): сверху её шапка, полоса рейса 1 — уже у нижнего края
            page.locator('#dpBoard .dp-blabel').first.click()
            page.wait_for_selector(CARD, timeout=5000)
            page.wait_for_selector(BAR + ':not([hidden])', timeout=5000)
            page.wait_for_timeout(500)
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            page.wait_for_timeout(200)
            side, bar = rect('#dpWsSide'), rect(BAR)
            check(abs(bar[3] - side[3]) <= 2 and bar[1] >= side[1], f'A trip bar fully at the bottom of the panel at scroll 0 {bar} / {side}')
            check(page.locator('#dpWsSide > :last-child').get_attribute('id') == 'dpWsTripBar', 'A trip bar is the last block of the panel')
            txt = page.locator(BAR).inner_text()
            check('Երթ 1' in txt and 'Քարտեզում' in txt and 'Փոփոխել' in txt and 'կմ' in txt and ('կգ' in txt or 'տ' in txt),
                  'A bar: trip title, «Քարտեզում», «Փոփոխել», km and weight')
            tops = page.locator(BAR + ' .dp-trip-acts .rt-btn').evaluate_all('bs => bs.map(b => Math.round(b.getBoundingClientRect().top))')
            check(len(tops) >= 2 and len(set(tops)) == 1, f'A bar buttons in one row {tops}')
            row = page.locator(BAR).evaluate("b => [b.querySelector('.dp-trip-t'), b.querySelector('.dp-loadm')]"
                                             ".flatMap(e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom]; })")
            check(row[0] < row[3] and row[2] < row[1], f'A title, time and load share one line {row}')
            check(bar[3] - bar[1] <= 0.45 * (side[3] - side[1]), f'A bar height {bar[3] - bar[1]:.0f} ≤ 45% of panel {side[3] - side[1]:.0f}')
            head = page.locator(CARD + ' .dp-trip-head').first
            check(head.locator('.dp-trip-t').is_visible() and head.locator('.dp-time').is_visible()
                  and not head.locator('.dp-trip-acts').is_visible() and not head.locator('.dp-trip-line').is_visible(),
                  'A in the list above the stores only «Երթ N · time», buttons and load are in the bar')
            page.screenshot(path=str(SHOTS / 'a-bar.png'))

            # B — строка магазина над полосой; Tab по «×»
            stop1 = page.locator(f'{CARD} .dp-stop').first
            stop1.evaluate(TO_TOP, 48)
            page.wait_for_timeout(200)
            side, bar, s1 = rect('#dpWsSide'), rect(BAR), stop1.evaluate(RECT)
            check(abs(bar[3] - side[3]) <= 2 and s1[3] <= bar[1] + 1 and s1[1] >= side[1] - 1, f'B store row visible above the bar {s1} / {bar}')
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            xs = page.locator(f'{CARD} .dp-stop-x')
            if xs.count():
                xs.last.focus()
                page.wait_for_timeout(200)
                xr, bar = xs.last.evaluate(RECT), rect(BAR)
                check(xr[3] <= bar[1] + 1, f'B focused «×» is not hidden under the bar {xr} / {bar}')

            # C — «Փոփոխել» в полосе
            tid = bar_trip()
            page.locator(BAR + ' .dp-editbtn').click()
            page.wait_for_selector(f'{CARD} .dp-trip.is-editing', timeout=5000)
            page.wait_for_timeout(300)
            side, bar = rect('#dpWsSide'), rect(BAR)
            check(bar_trip() == tid and abs(bar[3] - side[3]) <= 2, f'C editing: same trip in the bar, at the bottom {bar} / {side}')
            check(page.evaluate("!!document.activeElement && !!document.activeElement.closest('#dpWsTripBar .dp-editbtn')")
                  and 'Պատրաստ է' in page.locator(BAR + ' .dp-editbtn').inner_text(), 'C focus stays on the bar button, now «Պատրաստ է»')
            check(page.locator(f'{CARD} .dp-trip.is-editing .dp-trip-tools').is_visible(), 'C trip tools (truck, pin) shown in the list')
            page.screenshot(path=str(SHOTS / 'c-edit.png'))
            page.locator(BAR + ' .dp-editbtn').click()
            page.wait_for_timeout(300)
            check(page.locator(f'{CARD} .dp-trip.is-editing').count() == 0, 'C «Պատրաստ է» closes editing')

            # D — ноутбук
            page.set_viewport_size({'width': 1366, 'height': 768})
            page.wait_for_timeout(500)
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            page.wait_for_timeout(200)
            side, bar = rect('#dpWsSide'), rect(BAR)
            check(abs(bar[3] - side[3]) <= 2 and bar[1] >= side[1], f'D 1366×768: bar fully at the bottom at scroll 0 {bar} / {side}')
            stop1.evaluate(TO_TOP, 8)
            page.wait_for_timeout(200)
            side, bar, s1 = rect('#dpWsSide'), rect(BAR), stop1.evaluate(RECT)
            check(s1[3] <= bar[1] + 1 and s1[1] >= side[1] - 1, f'D 1366×768: a store row fits above the bar {s1} / {bar} / {side}')
            page.screenshot(path=str(SHOTS / 'd-laptop.png'))
            page.set_viewport_size({'width': 1600, 'height': 950})

            # M — машина с двумя рейсами: CAR2 (3,5 т), три магазина по 3,4 т — по одному на рейс (как «V» основной проверки)
            heavy = [_dorder(i + 1, cid, 3400.0, day=date(2026, 10, 1)) for i, cid in enumerate((101, 102, 104))]
            names = {101: ('C101', 'Клиент <101>'), 102: ('C102', 'Клиент 102'), 104: ('C104', 'Клиент 104')}
            app.extensions['route_optimizer'].dispatch_loader = lambda since, until, d: dp.DispatchData(
                tuple(heavy), names, {}, {1: ('CAR1',), 2: ('CAR2',)}, datetime(2026, 10, 1, 18, 0))
            build(DAY2, uncheck=('CAR1',))
            d = page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY2 + "').then(r => r.json())")
            truck = max(d['plan']['trucks'], key=lambda t: len(t['trips']))
            ids = [str(tr['id']) for tr in truck['trips']]
            check(len(ids) >= 2, f'M {truck["car_code"]} has {len(ids)} trips')
            if len(ids) >= 2:
                page.locator(f'#dpBoard .dp-blabel[data-truck="{truck["car_code"]}"]').first.click()
                page.wait_for_selector(BAR + ':not([hidden])', timeout=5000)
                page.wait_for_timeout(400)
                side_loc.evaluate('e => { e.scrollTop = 0; }')
                page.wait_for_timeout(300)
                check(bar_trip() == ids[0] and 'Երթ 1' in page.locator(BAR).inner_text(), 'M scroll 0 → «Երթ 1» in the bar')
                page.locator(f'{CARD} .dp-trip[data-trip="{ids[1]}"]').evaluate(TO_TOP, 40)
                page.wait_for_timeout(400)
                check(bar_trip() == ids[1] and 'Երթ 2' in page.locator(BAR).inner_text(), f'M scrolled to trip 2 → «Երթ 2» in the bar ({bar_trip()})')
                page.screenshot(path=str(SHOTS / 'm-trip2.png'))
                side_loc.evaluate('e => { e.scrollTop = 0; }')
                page.wait_for_timeout(400)
                check(bar_trip() == ids[0], 'M back to the top → «Երթ 1» again')
                page.locator(f'#dpBoard .dp-bar[data-trip="{ids[1]}"]').first.click()
                page.wait_for_timeout(900)
                side, bar = rect('#dpWsSide'), rect(BAR)
                check(bar_trip() == ids[1] and abs(bar[3] - side[3]) <= 2, f'M trip 2 clicked on the board → «Երթ 2» in the bar ({bar_trip()})')

            # E — обычный вид: полосы нет, шапка рейса с кнопками над точками, как раньше
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpTruckCards .dp-trip', state='attached', timeout=30000)
            page.wait_for_timeout(500)
            check(page.locator('#rtDispatch.is-ws').count() == 0, 'E list view is on')
            if not page.locator('#dpTruckCards .dp-trip').first.is_visible():
                page.locator('#dpTruckCards .dp-thead').first.click()      # карточка свёрнута — раскрыть
                page.wait_for_timeout(300)
            tr = page.locator('#dpTruckCards .dp-trip').first
            hd, st = tr.locator('.dp-trip-head').evaluate(RECT), tr.locator('.dp-stop').first.evaluate(RECT)
            check(hd[3] <= st[1] + 1 and tr.locator('.dp-trip-acts .dp-editbtn').is_visible(), f'E list view: head with buttons above its stores {hd} / {st}')
            check(page.locator(BAR + ':not([hidden])').count() == 0, 'E list view: no bottom trip bar')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERR  ' + e)
    check(not errors, f'no page/console errors ({len(errors)})')
    print(f'{sum(results)}/{len(results)} checks OK; screenshots in {SHOTS}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

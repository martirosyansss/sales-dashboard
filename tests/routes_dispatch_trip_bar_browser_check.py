# -*- coding: utf-8 -*-
"""Рабочий экран «Развоз»: в карточке выбранной машины (внизу) шапка рейса — отдельной полосой внизу панели, точки
прокручиваются над ней (владелец №89, 07.10). Настоящий браузер.

Запуск из корня проекта:  python tests/routes_dispatch_trip_bar_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py; порт 8775 на 127.0.0.1.

A выбрали машину с несколькими рейсами: полоса первого рейса прижата к низу панели, над ней видны точки;
  в полосе — «Երթ 1», время, кнопки одним рядом, загрузка и км; полоса не выше 45 % панели;
B прокрутили к последнему рейсу: внизу — его полоса, полоса первого ушла вверх вместе со своими точками и не
  перекрывает чужие точки; в самом низу последняя точка не спрятана под полосой;
C «Փոփոխել» в полосе: рейс в правке, полоса по-прежнему внизу, «Պատրաստ է» возвращает как было;
D ноутбук 1366×768: полоса и хотя бы одна точка видны;
E обычный вид (не рабочий экран): шапка рейса — над точками, как раньше.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

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
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8775
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-trip-bar-check'

RECT = "(e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"
CARD = '#dpWsSide .dp-tcard.is-focus'
# прокрутить только панель (не страницу), чтобы строка встала сразу под крестиком
TO_TOP = "(e) => { const p = document.getElementById('dpWsSide'); p.scrollTop += e.getBoundingClientRect().top - p.getBoundingClientRect().top - 48; }"


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
            rect = lambda loc: loc.evaluate(RECT)  # noqa: E731

            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#rtDispatch', timeout=30000)
            page.wait_for_function("() => document.getElementById('dpBuild') && !document.getElementById('dpBuild').disabled", timeout=15000)
            page.evaluate("document.getElementById('dpBuild').click()")
            page.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', timeout=30000)
            page.wait_for_timeout(800)
            if page.locator('#dpDrawer').is_visible():
                page.click('#dpDrawerClose')

            # машина с наибольшим числом рейсов (синтетический день — есть с двумя и больше)
            d = page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")
            truck = max(d['plan']['trucks'], key=lambda t: len(t['trips']))
            n_trips = len(truck['trips'])
            page.locator(f'#dpBoard .dp-bar[data-trip="{truck["trips"][0]["id"]}"]').first.click()
            page.wait_for_selector(CARD, timeout=5000)
            page.wait_for_timeout(500)
            side_loc = page.locator('#dpWsSide')
            if n_trips < 2:
                # в синтетическом дне у каждой машины один рейс — для раскладки копируем его блок (проверяется только CSS)
                page.evaluate("""() => { const t = document.querySelector('#dpWsSide .dp-tcard.is-focus .dp-trip');
                    const c = t.cloneNode(true); c.dataset.trip = 'copy'; c.querySelector('.dp-trip-t').textContent = 'Երթ 2';
                    t.after(c); }""")
                n_trips = 2
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            page.wait_for_timeout(200)
            side = rect(side_loc)
            heads = page.locator(CARD + ' .dp-trip-head')
            check(heads.count() == n_trips, f'A {n_trips} trips, {heads.count()} trip bars')

            # A — полоса первого рейса прижата к низу панели, её точки прокручиваются над ней
            h1 = rect(heads.nth(0))
            check(abs(h1[3] - side[3]) <= 2, f'A first trip bar sits at the bottom of the panel (bar bottom {h1[3]:.0f}, panel {side[3]:.0f})')
            stop1 = page.locator(f'{CARD} .dp-trip').nth(0).locator('.dp-stop').first
            stop1.evaluate(TO_TOP)
            page.wait_for_timeout(200)
            s1, h1, side = rect(stop1), rect(heads.nth(0)), rect(side_loc)
            check(s1[3] <= h1[1] + 1 and s1[1] >= side[1] - 1 and abs(h1[3] - side[3]) <= 2,
                  f'A first store row scrolled up is visible above the bar {s1} / {h1}')
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            page.wait_for_timeout(200)
            h1 = rect(heads.nth(0))
            txt = heads.nth(0).inner_text()
            check('Երթ 1' in txt and 'Քարտեզում' in txt and 'կմ' in txt and ('կգ' in txt or 'տ' in txt), 'A bar has trip title, «Քարտեզում», km and weight')
            tops = heads.nth(0).locator('.dp-trip-acts .rt-btn').evaluate_all('bs => bs.map(b => Math.round(b.getBoundingClientRect().top))')
            check(len(tops) >= 2 and len(set(tops)) == 1, f'A bar buttons in one row {tops}')
            fits = heads.nth(0).locator('.dp-trip-acts .rt-btn').evaluate_all(
                'bs => bs.every(b => b.getBoundingClientRect().right <= b.closest(".dp-trip-head").getBoundingClientRect().right + 1)')
            check(fits, 'A bar buttons do not overflow the bar')
            row = heads.nth(0).evaluate("h => [h.querySelector('.dp-trip-t'), h.querySelector('.dp-loadm')].flatMap(e => { const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.bottom)]; })")
            check(row[0] < row[3] and row[2] < row[1], f'A title, time and load share one line {row}')
            check(h1[3] - h1[1] <= 0.45 * (side[3] - side[1]), f'A bar height {h1[3] - h1[1]:.0f} ≤ 45% of panel {side[3] - side[1]:.0f}')
            page.screenshot(path=str(SHOTS / 'a-bar.png'))

            # B — прокрутка к последнему рейсу
            last = page.locator(f'{CARD} .dp-trip').nth(n_trips - 1)
            last.locator('.dp-stop').first.evaluate(TO_TOP)
            page.wait_for_timeout(200)
            side = rect(side_loc)
            hl, h1 = rect(heads.nth(n_trips - 1)), rect(heads.nth(0))
            check(abs(hl[3] - side[3]) <= 2, f'B last trip bar sits at the bottom of the panel {hl} / {side}')
            first_stops_end = rect(page.locator(f'{CARD} .dp-trip').nth(0).locator('.dp-stop').last)
            check(h1[1] >= first_stops_end[3] - 1, 'B first trip bar went up with its own stops (it follows them)')
            # прокрутили за конец рейса: полоса ушла вверх со своей последней точкой, точка видна над ней, а не под ней
            last.evaluate("t => { const p = document.getElementById('dpWsSide'), h = t.querySelector('.dp-trip-head');"
                          " const s = [...t.querySelectorAll('.dp-stop')].pop();"
                          " p.scrollTop += s.getBoundingClientRect().bottom - (p.getBoundingClientRect().bottom - h.offsetHeight) + 20; }")
            page.wait_for_timeout(200)
            side, hl, lst = rect(side_loc), rect(heads.nth(n_trips - 1)), rect(last.locator('.dp-stop').last)
            check(hl[3] <= side[3] - 10 and side[1] <= lst[1] and lst[3] <= hl[1] + 1,
                  f'B end of the last trip: its last store row is visible right above the bar {lst} / {hl} / {side}')
            # Tab на «×» точки — кнопка не под полосой (scroll-padding-bottom)
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            xs = page.locator(f'{CARD} .dp-trip').nth(0).locator('.dp-stop-x')
            if xs.count():
                xs.last.focus()
                page.wait_for_timeout(200)
                xr, hb = rect(xs.last), rect(heads.nth(0))
                check(xr[3] <= hb[1] + 1, f'B focused «×» is not hidden under the bar {xr} / {hb}')
            page.screenshot(path=str(SHOTS / 'b-last.png'))

            # C — «Փոփոխել» в полосе
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            heads.nth(0).locator('.dp-editbtn').click()
            page.wait_for_selector(f'{CARD} .dp-trip.is-editing', timeout=5000)
            page.wait_for_timeout(300)
            side_loc.evaluate('e => { e.scrollTop = 0; }')
            page.wait_for_timeout(200)
            side, h1 = rect(side_loc), rect(page.locator(f'{CARD} .dp-trip.is-editing .dp-trip-head'))
            check(abs(h1[3] - side[3]) <= 2, f'C editing: the bar stays at the bottom {h1} / {side}')
            page.screenshot(path=str(SHOTS / 'c-edit.png'))
            page.locator(f'{CARD} .dp-trip.is-editing .dp-editbtn').click()
            page.wait_for_timeout(300)
            check(page.locator(f'{CARD} .dp-trip.is-editing').count() == 0, 'C «Պատրաստ է» closes editing')

            # D — ноутбук
            page.set_viewport_size({'width': 1366, 'height': 768})
            page.wait_for_timeout(500)
            stop1.evaluate(TO_TOP.replace('- 48', '- 8'))     # низкая панель: строку — к самому верху
            page.wait_for_timeout(200)
            side, h1, s1 = rect(side_loc), rect(heads.nth(0)), rect(stop1)
            check(h1[3] <= side[3] + 1 and s1[3] <= h1[1] + 1 and s1[1] >= side[1], f'D 1366×768: bar and a store row visible {s1} / {h1} / {side}')
            tops = heads.nth(0).locator('.dp-trip-acts .rt-btn').evaluate_all('bs => bs.map(b => Math.round(b.getBoundingClientRect().top))')
            check(len(set(tops)) == 1, f'D 1366×768: bar buttons in one row {tops}')
            page.screenshot(path=str(SHOTS / 'd-laptop.png'))
            page.set_viewport_size({'width': 1600, 'height': 950})

            # E — обычный вид: шапка рейса над точками, как раньше
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpTruckCards .dp-trip', state='attached', timeout=30000)
            if page.locator('#rtDispatch.is-ws').count():
                page.locator('.dp-view-list, [data-layout="list"]').first.click()
                page.wait_for_timeout(500)
            if page.locator('#rtDispatch.is-ws').count() == 0:
                if not page.locator('#dpTruckCards .dp-trip').first.is_visible():
                    page.locator('#dpTruckCards .dp-thead').first.click()      # карточка свёрнута — раскрыть
                    page.wait_for_timeout(300)
                tr = page.locator('#dpTruckCards .dp-trip').first
                hd, st = rect(tr.locator('.dp-trip-head')), rect(tr.locator('.dp-stop').first)
                check(hd[3] <= st[1] + 1, f'E list view: trip head is above its stores {hd} / {st}')
                check(tr.locator('.dp-trip-head').evaluate('e => getComputedStyle(e).position') == 'static', 'E list view: head is not sticky')
            else:
                check(False, 'E could not switch to the list view')
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

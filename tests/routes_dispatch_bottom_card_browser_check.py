# -*- coding: utf-8 -*-
"""Рабочий экран «Развоз» (/routes/dispatch): карточка выбранной машины внизу, а не поверх карты; точка на карте —
карточка магазина с «Հանել երթից» (владелец 07.10). Настоящий браузер.

Запуск из корня проекта:  python tests/routes_dispatch_bottom_card_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py; порт 8774 на 127.0.0.1.

A рабочий экран, рейсы собраны: карточки машины нет, карта на всю ширину;
B нажали машину на шкале: карточка — в нижней полосе справа от шкалы, не пересекает карту; карта стала ниже, но видна;
C нажали точку на карте: строка магазина в карточке подсвечена, у точки — карточка магазина (название, машина, заказы)
  с «Հանել երթից»;
D «Հանել երթից» → одна правка trip_stops remove [cid] этого рейса, карточка магазина закрыта, подсказка «հանվեց»,
  магазин — в «Դեռ երթերում չեն»;
E «×» карточки машины → она закрыта, карта снова до шкалы;
F все машины (ничего не выбрано): точка → карточка машины внизу и карточка магазина;
P прошедший день: у карточки магазина нет «Հանել երթից».
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
from route_optimizer import views  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8774
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-bottom-card-check'

RECT = "(s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-bottom-card-'), FakeClient())
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)     # «сейчас» — накануне DAY: день не прошедший, правки доступны
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1600, 'height': 950}).new_page()
            page.add_init_script("try { localStorage.setItem('dpLayout', 'ws'); } catch (e) {}")
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                    if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            data = lambda: page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")  # noqa: E731
            edits = lambda n: [p[1] for p in posts[n:] if p[0] == 'edit']  # noqa: E731
            rect = lambda s: page.evaluate(RECT, s)  # noqa: E731

            def trip_of(d, cid):
                return next((tr['id'] for t in d['plan']['trucks'] for tr in t['trips']
                             if any(s['customer_id'] == cid for s in tr['stops'])), None)

            # A — сборка в выдвижной панели шагов, затем рабочий экран
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#rtDispatch', timeout=30000)
            page.wait_for_function("() => document.getElementById('dpBuild') && !document.getElementById('dpBuild').disabled", timeout=15000)
            page.evaluate("document.getElementById('dpBuild').click()")
            page.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', timeout=30000)
            page.wait_for_timeout(800)
            if page.locator('#dpDrawer').is_visible():
                page.click('#dpDrawerClose')
            check(page.locator('#dpWsSide').is_hidden(), 'A no truck card while nothing is selected')
            map0 = rect('#dpWsMap')
            ws = rect('#dpWs')
            check(map0 and abs(map0[2] - ws[2]) < 3, f'A map spans the full width {map0} / {ws}')
            page.screenshot(path=str(SHOTS / 'a-ws.png'))

            # B — машина на шкале: карточка внизу справа от шкалы, не над картой
            page.locator('#dpBoard .dp-blabel').first.click()
            page.wait_for_selector('#dpWsSide:not([hidden]) .dp-tcard.is-focus', timeout=5000)
            page.wait_for_timeout(500)
            side, mp, low, bottom = rect('#dpWsSide'), rect('#dpWsMap'), rect('.dp-ws-low'), rect('#dpWsBottom')
            check(page.locator('.dp-ws-low > #dpWsSide').count() == 1 and page.locator('.dp-ws-main #dpWsSide').count() == 0,
                  'B truck card lives in the bottom strip, not in the map block')
            check(side[1] >= mp[3] - 1, f'B card is below the map (card top {side[1]:.0f} ≥ map bottom {mp[3]:.0f})')
            check(side[0] >= bottom[2] - 1 and abs(side[2] - low[2]) < 2, f'B card is to the right of the day board {side} / {bottom}')
            check(mp[3] - mp[1] >= 300 and mp[3] - mp[1] < map0[3] - map0[1], f'B map is lower but still big ({mp[3] - mp[1]:.0f} px)')
            size = page.evaluate("(() => { const m = document.getElementById('dpMap'); return [m.clientWidth, m.clientHeight]; })()")
            check(abs(size[1] - (mp[3] - mp[1])) < 3, f'B Leaflet container follows the new map height {size}')
            check(page.locator('#dpWsSide .dp-tcard.is-focus .dp-stop').first.is_visible(), 'B store rows visible in the card')
            page.screenshot(path=str(SHOTS / 'b-truck.png'))

            # C — точка на карте: строка подсвечена, карточка магазина у точки
            pin = page.locator('.dp-ws-map .dp-npin').first
            tr_id = pin.locator('span').get_attribute('data-trip')
            pin.click(force=True)
            page.wait_for_selector('.leaflet-popup .dp-stopcard', timeout=5000)
            card = page.locator('.leaflet-popup .dp-stopcard')
            flashed = page.locator('#dpWsSide .dp-stop.is-flash')
            check(flashed.count() == 1, 'C the store row flashes in the truck card')
            cid = int(flashed.first.get_attribute('data-cid')) if flashed.count() else None
            d = data()
            stop = next((s for t in d['plan']['trucks'] for tr in t['trips'] if str(tr['id']) == tr_id
                         for s in tr['stops'] if s['customer_id'] == cid), None)
            txt = card.inner_text()
            check(stop is not None and (stop['name'] or stop['code']) in txt and 'երթ' in txt and 'Քաշ' in txt,
                  'C store card: name, truck/trip, weight')
            check(card.locator('.dp-stopcard-orders li').count() == len(stop['orders']) if stop else False,
                  f'C store card lists its orders ({card.locator(".dp-stopcard-orders li").count()})')
            check(card.locator('.dp-stopcard-x').is_visible(), 'C «Հանել երթից» in the store card')
            page.mouse.move(5, 5)
            page.wait_for_timeout(400)                        # появление карточки — анимация Leaflet
            page.screenshot(path=str(SHOTS / 'c-stopcard.png'))

            # D — «Հանել երթից»
            n = len(posts)
            card.locator('.dp-stopcard-x').click()
            page.wait_for_selector('#dpToast.is-on .dp-toast-act', timeout=15000)
            e = edits(n)
            check(len(e) == 1 and e[0].get('action') == 'trip_stops' and e[0].get('remove') == [cid] and str(e[0].get('trip')) == tr_id,
                  'D one trip_stops remove edit for that trip: ' + str(e))
            page.wait_for_timeout(400)                        # закрытие — после анимации Leaflet
            check(page.locator('.leaflet-popup .dp-stopcard').count() == 0, 'D store card closed')
            check('հանվեց' in page.locator('#dpToast').inner_text(), 'D toast says removed, with «Չեղարկել»')
            check(page.evaluate("document.activeElement && document.activeElement.classList.contains('dp-toast-act')"),
                  'D focus moves to «Չեղարկել» (the card button is gone)')
            d = data()
            check(cid in [s['customer_id'] for s in d['plan']['unassigned']] and trip_of(d, cid) is None, 'D store is now not in trips')
            page.screenshot(path=str(SHOTS / 'd-removed.png'))

            # E — закрыть карточку машины
            page.click('#dpWsClose')
            page.wait_for_timeout(400)
            # над рабочим экраном мог появиться счётчик «Դեռ երթերում չեն» — сравниваем долю карты, не пиксели
            mp, ws1 = rect('#dpWsMap'), rect('#dpWs')
            share0, share1 = (map0[3] - map0[1]) / (ws[3] - ws[1]), (mp[3] - mp[1]) / (ws1[3] - ws1[1])
            check(page.locator('#dpWsSide').is_hidden() and page.locator('#dpWs.has-side').count() == 0
                  and abs(share1 - share0) < .02, f'E card closed, map back to its share ({share0:.2f} → {share1:.2f})')

            # F — все машины: точка → карточка машины внизу и карточка магазина
            page.locator('.dp-ws-map .dp-dpin').first.click(force=True)
            page.wait_for_selector('#dpWsSide:not([hidden]) .dp-tcard.is-focus', timeout=5000)
            page.wait_for_selector('.leaflet-popup .dp-stopcard', timeout=5000)
            check(page.locator('.leaflet-popup .dp-stopcard .dp-stopcard-x').is_visible() and page.locator('#dpWsSide .dp-stop.is-flash').count() == 1,
                  'F all trucks: point → truck card below and the store card')
            page.keyboard.press('Escape')
            page.wait_for_timeout(200)
            check(page.locator('.leaflet-popup .dp-stopcard').count() == 0, 'F Esc closes the store card')

            # S — низкий экран ноутбука: карта ниже, но кнопка карточки видна целиком внутри карты; Esc на кнопке закрывает
            page.set_viewport_size({'width': 1366, 'height': 768})
            page.wait_for_timeout(500)
            page.locator('.dp-ws-map .dp-npin, .dp-ws-map .dp-dpin').first.click(force=True)
            page.wait_for_selector('.leaflet-popup .dp-stopcard-x', timeout=5000)
            page.wait_for_timeout(500)
            btn, mp = rect('.leaflet-popup .dp-stopcard-x'), rect('#dpWsMap')
            vis = page.evaluate("(() => { const b = document.querySelector('.leaflet-popup .dp-stopcard-x'), r = b.getBoundingClientRect();"
                                " const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!e && b.contains(e); })()")
            check(btn[1] >= mp[1] and btn[3] <= mp[3] + 1 and vis, f'S 1366×768: «Հանել երթից» fully visible in the map {btn} / {mp}')
            page.screenshot(path=str(SHOTS / 's-laptop.png'))
            page.locator('.leaflet-popup .dp-stopcard-x').focus()
            page.keyboard.press('Escape')
            page.wait_for_timeout(400)
            check(page.locator('.leaflet-popup .dp-stopcard').count() == 0, 'S Esc on the card button closes the card')
            # другой день — карточка закрыта и не держит автообновление
            page.locator('.dp-ws-map .dp-npin, .dp-ws-map .dp-dpin').first.click(force=True)
            page.wait_for_selector('.leaflet-popup .dp-stopcard', timeout=5000)
            page.click('#dpDayNext')
            page.wait_for_timeout(1500)
            check(page.locator('.leaflet-popup .dp-stopcard').count() == 0, 'S next day: the store card is closed')
            page.set_viewport_size({'width': 1600, 'height': 950})

            # P — прошедший день: без «Հանել երթից»
            views._clock = lambda: datetime(2026, 10, 2, 18, 0)
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', timeout=30000)
            page.wait_for_timeout(800)
            page.locator('.dp-ws-map .dp-dpin').first.click(force=True)
            page.wait_for_selector('.leaflet-popup .dp-stopcard', timeout=5000)
            check(page.locator('.leaflet-popup .dp-stopcard-x').count() == 0, 'P past day: no «Հանել երթից» in the store card')
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

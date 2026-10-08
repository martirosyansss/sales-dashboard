# -*- coding: utf-8 -*-
"""«Խանութներ, որոնց տեղը քարտեզում չկա» на «Развозе» (владелец 08.10 «не профессионально») в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_no_coords_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
из tests/routes_dispatch_browser_check.py (клиент 999 без точки); ответу /api/routes/dispatch подставлен адрес 999,
чтобы проверить поиск на Яндекс Картах. Порт 8777 на 127.0.0.1.

A раздел виден, в заголовке «1 խանութ · 50 կգ»; таблица: шапка из трёх колонок ровно над ячейками, одна строка —
  имя, код, адрес | вес, сумма | иконка Yandex (поиск адреса в новой вкладке) и «Նշել քարտեզում»;
  старых инструкции и встроенной карты нет;
B «Նշել քարտեզում» → диалог «Խանութի տեղը»: заголовок с именем, ссылка «Գտնել հասցեն Yandex քարտեզում» с адресом,
  «Պահպանել» недоступна; клик по карте → координаты в поле, «Պահպանել» доступна, «Վերադարձնել ավտոմատ» скрыта;
C «Չեղարկել» → диалог закрыт, запроса нет; снова открыть → поле пустое;
D вставить координаты → «Պահպանել» → POST /api/routes/geo-override {999, lat, lon}; диалог закрыт, сообщение
  «կետը պահպանված է», раздел исчез (у магазина есть точка);
E телефон 390×860: строка в три линии без шапки, нет горизонтальной прокрутки.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8777
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
ADDRESS = 'Երևան, Արաբկիր, Ադբյուր Սեռոբ 52/12'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-nocoords-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1440, 'height': 950})
            page = ctx.new_page()
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('request', lambda r: posts.append(r.post_data_json) if r.method == 'POST' and '/api/routes/geo-override' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))

            def with_address(route):
                resp = route.fetch()
                data = resp.json()
                for s in data.get('stops_no_coords') or []:
                    if s['customer_id'] == 999:
                        s['address'] = ADDRESS
                route.fulfill(response=resp, body=json.dumps(data), content_type='application/json')
            page.route('**/api/routes/dispatch?date=*', with_address)

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_selector('#dpNoCoords', state='visible', timeout=15000)
            check(page.inner_text('#dpNoCoordsNote').replace(' ', ' ') == '1 խանութ · 50 կգ',
                  'A summary note: ' + page.inner_text('#dpNoCoordsNote'))
            page.click('#dpNoCoords > summary')
            page.wait_for_selector('#dpNoCoordsList .dp-nc-row', state='visible', timeout=5000)
            rows = page.locator('#dpNoCoordsList .dp-nc-row')
            row = rows.first
            text = row.inner_text()
            check(rows.count() == 1 and ADDRESS in text and '50' in text and 'դրամ' in text,
                  'A one row with address, kg and sum: ' + text.replace('\n', ' | '))
            check(page.locator('#dpNoCoords .dp-nc-hd').is_visible(), 'A table header visible on desktop')
            ya = row.locator('a.rt-btn')
            href = ya.get_attribute('href') or ''
            check(ya.count() == 1 and href.startswith('https://yandex.com/maps/?ll=44.5126%2C40.1811&z=12&text=') and ya.get_attribute('target') == '_blank'
                  and 'noopener' in (ya.get_attribute('rel') or ''), 'A Yandex search link opens new tab: ' + href[:60])
            check(row.locator('button', has_text='Նշել քարտեզում').count() == 1, 'A button «Նշել քարտեզում»')
            check(page.locator('#dpPickMap, .dp-howto, #dpPickList').count() == 0, 'A old howto/inline map removed')
            hd = page.evaluate("() => [...document.querySelectorAll('#dpNoCoords .dp-nc-hd span')].map(s => Math.round(s.getBoundingClientRect().left))")
            cells = page.evaluate("() => [...document.querySelectorAll('#dpNoCoordsList .dp-nc-row > div')].map(s => Math.round(s.getBoundingClientRect().left))")
            check(len(hd) == 3 and hd == cells, f'A header columns aligned with row cells: {hd} vs {cells}')

            # B
            row.locator('button', has_text='Նշել քարտեզում').click()
            page.wait_for_selector('#dpGeoDlg[open]', timeout=5000)
            check('«' in page.inner_text('#dpGeoTitle') and 'խանութի տեղը' in page.inner_text('#dpGeoTitle'),
                  'B dialog title: ' + page.inner_text('#dpGeoTitle'))
            find = page.locator('#dpGeoFind')
            check(find.is_visible() and (find.get_attribute('href') or '') == href, 'B Yandex link in dialog = row link')
            check(page.locator('#dpGeoSave').is_disabled() and not page.locator('#dpGeoAuto').is_visible(),
                  'B save disabled until a point, «auto» hidden')
            mh = page.evaluate("() => document.getElementById('dpGeoMap').getBoundingClientRect().height")
            check(mh >= 280, f'B dialog map is large: {mh}px')
            page.locator('#dpGeoMap').click(position={'x': 200, 'y': 150})
            check(page.input_value('#dpGeoCoord').count(',') == 1 and page.locator('#dpGeoSave').is_enabled(),
                  'B map click fills coords and enables save: ' + page.input_value('#dpGeoCoord'))

            # C
            page.click('#dpGeoCancel')
            check(not page.locator('#dpGeoDlg[open]').count() and not posts, 'C cancel closes, nothing sent')
            row.locator('button', has_text='Նշել քարտեզում').click()
            page.wait_for_selector('#dpGeoDlg[open]', timeout=5000)
            check(page.input_value('#dpGeoCoord') == '' and page.locator('#dpGeoSave').is_disabled(), 'C reopened empty')

            # E (до сохранения — раздел ещё есть)
            page.click('#dpGeoCancel')
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check(over <= 0, f'E phone: no horizontal scroll (overflow {over}px)')
            if page.locator('#dpNoCoords .dp-nc-hd').is_visible():
                check(False, 'E phone: header hidden')
            else:
                check(True, 'E phone: header hidden')
            page.set_viewport_size({'width': 1440, 'height': 950})
            page.wait_for_timeout(300)

            # D
            row = page.locator('#dpNoCoordsList .dp-nc-row').first
            row.locator('button', has_text='Նշել քարտեզում').click()
            page.wait_for_selector('#dpGeoDlg[open]', timeout=5000)
            page.fill('#dpGeoCoord', '40.20510, 44.51320')
            check(page.locator('#dpGeoSave').is_enabled(), 'D pasted coords enable save')
            page.click('#dpGeoSave')
            page.wait_for_selector('#dpGeoDlg:not([open])', state='attached', timeout=5000)
            page.wait_for_selector('#dpNoCoords', state='hidden', timeout=10000)
            check(posts == [{'customer_id': 999, 'lat': 40.2051, 'lon': 44.5132}], f'D one geo-override POST: {posts}')
            toast = page.locator('.dp-toast').last.inner_text() if page.locator('.dp-toast').count() else ''
            check('կետը պահպանված է' in toast, 'D toast: ' + toast)
            check(not page.locator('#dpNoCoords').is_visible(), 'D section gone after the point is saved')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERROR', e)
    ok = all(results) and not errors
    print(f'{sum(results)}/{len(results)} checks passed' + ('' if not errors else f', {len(errors)} page errors'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Список «Վարորդների փոխած կետերը» на «Առաքում» (владелец 10.11.2026): точки из отметок водителей и их отмена.

Запуск из корня проекта:  DB_USER=x python tests/routes_dispatch_geo_marks_browser_check.py [папка для снимка]
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и стенд — как в
tests/routes_dispatch_browser_check.py; ответу /api/routes/dispatch подставлен список geo_marks.

A список виден, в нём все строки; текст строки: водитель, время, метры, откуда точка
B кнопка отмены только у действующей; отменённая и изменённая подписаны
C в раскрытом блоке карта со старой и новой точкой; выбор строки меняет её
D «Չեղարկել» шлёт event_id, после ответа строка становится отменённой
E ответ 409: ошибка показана, список перечитан
G плашка на готовом плане ведёт к списку без ручного раскрытия
F нет отметок — блока нет
Ошибки страницы и консоли — провал (кроме внешних ресурсов и ожидаемого 409).
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

PORT = 8792
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
A, B, C, D = ('aaaaaaaa-1111-4111-8111-111111111111', 'bbbbbbbb-1111-4111-8111-111111111111',
              'cccccccc-1111-4111-8111-111111111111', 'dddddddd-1111-4111-8111-111111111111')


def mark(event_id, name, **kw):
    return {'event_id': event_id, 'customer_id': 101, 'code': '9819', 'name': name, 'lat': 40.2, 'lon': 44.52, 'driver_name': 'Արամ',
            'at': '2026-11-10T14:00:57+04:00', 'source': 'gps', 'in_day': True, 'can_undo': True, 'undone': False,
            'current': {'lat': 40.19, 'lon': 44.5, 'source': None}, 'distance_m': 1842, **kw}


ROWS = """() => [...document.querySelectorAll('#dpGeoMarksList .dp-sug')].map(li => ({
    name: li.querySelector('b').textContent, text: li.querySelector('.dp-pick-addr').textContent,
    undo: !!li.querySelector('.dp-sug-acts button') }))"""


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-geomarks-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    items = [mark(A, 'Խանութ Ա', source='map'), mark(B, 'Խանութ Բ', can_undo=False), mark(C, 'Խանութ Գ', can_undo=False, undone=True),
             mark(D, 'Խանութ Դ')]
    undo_calls = []
    loads = [0]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1920, 'height': 945})
            ctx.add_init_script("try { if (!localStorage.getItem('dpLayout')) localStorage.setItem('dpLayout', 'ws'); } catch (e) {}")
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                ctx.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))

            def dispatch(route):
                resp = route.fetch()
                data = resp.json()
                data['is_past'] = False
                data['geo_marks'] = {'items': items}
                loads[0] += 1
                route.fulfill(response=resp, body=json.dumps(data), content_type='application/json')
            ctx.route('**/api/routes/dispatch?date=*', dispatch)

            def undo(route):
                body = json.loads(route.request.post_data)
                undo_calls.append(body)
                if body['event_id'] == D:
                    items[3].update(can_undo=False)
                    route.fulfill(status=409, content_type='application/json',
                                  body=json.dumps({'success': False, 'error': 'Կետն արդեն փոխվել է — թարմացրեք էջը'}))
                    return
                items[0].update(can_undo=False, undone=True)
                route.fulfill(status=200, content_type='application/json', body=json.dumps({'success': True, 'customer_id': 101}))
            ctx.route('**/api/routes/geo-marks/undo', undo)

            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text)
                    if m.type == 'error' and not is_ignorable(m) and '409' not in m.text else None)

            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_timeout(800)

            # A, B
            hidden = page.evaluate("document.getElementById('dpGeoMarks').hidden")
            check(hidden is False, 'A блок показан')
            rows = page.evaluate(ROWS)
            print('     ', rows[0])
            check([r['name'] for r in rows] == ['Խանութ Ա', 'Խանութ Բ', 'Խանութ Գ', 'Խանութ Դ'], 'A все строки, порядок сервера')
            first = rows[0]['text']
            check('Վարորդ՝ Արամ' in first and '10.11 14:00' in first and '1 842' in first.replace('\xa0', ' ').replace(',', ' ') and 'քարտեզից' in first,
                  'A водитель, время, метры, источник «քարտեզից»')
            check('GPS-ով' in rows[1]['text'], 'A источник GPS')
            check(rows[0]['undo'] and not rows[1]['undo'] and not rows[2]['undo'] and rows[3]['undo'], 'B кнопка только у действующих')
            check('կետն արդեն փոխվել է' in rows[1]['text'], 'B изменённая подписана')
            check('չեղարկված է' in rows[2]['text'], 'B отменённая подписана')
            check(page.evaluate("document.getElementById('dpGeoMarksNote').textContent") == '4', 'A число в заголовке')

            # C
            page.evaluate("""() => { let e = document.getElementById('dpGeoMarks');
                for (let p = e; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true;
                for (let p = e.parentElement; p; p = p.parentElement) p.hidden = false; }""")
            page.wait_for_timeout(600)
            check(page.locator('#dpGeoMarksMap.leaflet-container').count() == 1, 'C карта создана')
            markers = "document.querySelectorAll('#dpGeoMarksMap path.leaflet-interactive').length"
            check(page.evaluate(markers) == 3, 'C старая точка, новая и линия между ними')
            check(page.evaluate("document.querySelector('#dpGeoMarksList .dp-pickitem').getAttribute('aria-pressed')") == 'true', 'C первая выбрана')
            page.evaluate("document.querySelectorAll('#dpGeoMarksList .dp-pickitem')[1].click()")
            page.wait_for_timeout(200)
            pressed = page.evaluate("[...document.querySelectorAll('#dpGeoMarksList .dp-pickitem')].map(b => b.getAttribute('aria-pressed'))")
            check(pressed[:2] == ['false', 'true'], 'C выбор строки')
            if shots:
                shots.mkdir(parents=True, exist_ok=True)
                page.locator('#dpGeoMarks').screenshot(path=str(shots / 'geo-marks.png'))

            # D
            before = loads[0]
            page.evaluate("document.querySelector('#dpGeoMarksList .dp-sug-acts button').click()")
            page.wait_for_function(f"() => document.querySelector('#dpGeoMarksList .dp-sug .dp-pick-addr').textContent.includes('չեղարկված է')", timeout=10000)
            check(undo_calls == [{'event_id': A}], 'D отправлен event_id')
            check(loads[0] > before, 'D список перечитан')
            check(not page.evaluate(ROWS)[0]['undo'], 'D кнопки больше нет')

            # E
            before = loads[0]
            page.evaluate("[...document.querySelectorAll('#dpGeoMarksList .dp-sug')][3].querySelector('.dp-sug-acts button').click()")
            page.wait_for_function("() => document.querySelectorAll('#dpGeoMarksList .dp-sug')[3].querySelector('.dp-pick-addr').textContent.includes('կետն արդեն փոխվել է')", timeout=10000)
            check(len(undo_calls) == 2 and undo_calls[1] == {'event_id': D}, 'E отправлен запрос')
            check(loads[0] > before, 'E список перечитан')
            check('Կետն արդեն փոխվել է' in page.inner_text('body'), 'E ошибка показана')

            # G: настоящая дорога к списку — плашка на готовом плане, без ручного раскрытия
            items[:] = [mark(A, 'Խանութ Ա', at=DAY + 'T10:00:00+04:00'), mark(B, 'Խանութ Բ', at=DAY + 'T11:00:00+04:00', undone=True, can_undo=False)]
            page.reload()
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.evaluate("document.getElementById('dpPrepOpen')?.click()")
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            page.evaluate("document.getElementById('dpBuild').click()")
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.reload()
            page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            page.wait_for_timeout(800)
            chip = page.locator('#dpStoreBar .dp-stcell', has_text='Վարորդների կետեր')
            check(chip.count() == 1 and chip.locator('b').inner_text() == '1', 'G плашка: только неотменённые за этот день')
            check(page.locator('#dpGeoMarks').is_hidden(), 'G до нажатия список свёрнут вместе с шагом')
            chip.click()
            page.wait_for_selector('#dpGeoMarks[open]', timeout=10000)
            page.wait_for_timeout(500)
            check(page.locator('#dpGeoMarks').is_visible(), 'G плашка раскрывает список')
            check(page.locator('#dpGeoMarksList .dp-sug').first.is_visible(), 'G строка видна')
            check(page.locator('#dpGeoMarksMap.leaflet-container').count() == 1, 'G карта есть')

            # F
            items.clear()
            page.reload()
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_timeout(800)
            check(page.evaluate("document.getElementById('dpGeoMarks').hidden") is True, 'F без отметок блока нет')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, f'нет ошибок страницы и консоли {errors[:3]}')
    print(f'{sum(results)} из {len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

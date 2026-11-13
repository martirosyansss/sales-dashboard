# -*- coding: utf-8 -*-
"""«Առաքում» без рейсов (владелец 10.11.2026): вида-списка на широком экране нет, собранный план без рейсов — рабочий экран.

Запуск из корня проекта:  python tests/routes_dispatch_empty_plan_browser_check.py [папка для снимков]
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение — из
tests/routes_dispatch_browser_check.py, день завтрашний. План собран, отправлен и опустошён настоящими правками (defer_store),
как у логиста 10.11: у водителя версия с магазинами, на странице рейсов нет. Порт 8811.

A рабочий экран, карта, чип «Վերակազմել երթերը», полоса магазинов, «Ուղարկել», переключателей вида нет;
B кнопка пошаговой подготовки доступна и открывает её;
C магазины вне рейсов: счётчик есть и открывает список;
D нет машин дня — не пишем, что все в рейсах;
E сохранённый раньше выбор списка не действует;
F узкое окно (меньше 1101): рабочего экрана нет, подсказка дня и «Վերադարձնել» видны;
G «Վերադարձնել ուղարկված երթերը» из меню «⋯» возвращает рейсы, экран остаётся рабочим.
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

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

PORT = 8811
BASE = f'http://127.0.0.1:{PORT}'
TODAY, DAY = '2026-09-30', '2026-10-01'
GEOM = """() => { const w = document.getElementById('dpWs').getBoundingClientRect();
    return { wsBottom: w.bottom, win: innerHeight, side: document.documentElement.scrollWidth - document.documentElement.clientWidth }; }"""


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-empty-'), FakeClient())
    state = app.extensions['route_optimizer']
    state.store.save_truck_driver('CAR1', TODAY, 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', TODAY, 'Կարեն', 'qa')
    views._clock = lambda: datetime(2026, 9, 30, 18, 0)
    views._yerevan_now = lambda: datetime(2026, 9, 30, 18, 0, tzinfo=YEREVAN)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    mods = {'unassigned': None, 'no_trucks': False}

    def tweak(route):
        if route.request.method != 'GET':
            return route.continue_()
        resp = route.fetch()
        body = resp.json()
        if mods['unassigned'] and body.get('plan'):
            body['plan']['unassigned'] = mods['unassigned']
        if mods['no_trucks']:
            body['trucks'] = []
        return route.fulfill(response=resp, json=body)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1354, 'height': 860})
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                ctx.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            ctx.route(re.compile(r'/api/routes/dispatch\?date='), tweak)
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)

            def day():
                return page.request.get(f'{BASE}/api/routes/dispatch?date={DAY}').json()

            def edit(**body):
                resp = page.request.post(f'{BASE}/api/routes/dispatch/edit', data={'date': DAY, 'rev': day()['rev'], **body})
                data = resp.json()
                assert resp.ok and data.get('success', True), data
                return data

            def reload_ws():
                page.reload()
                page.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
                page.wait_for_timeout(900)

            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            check(page.locator('#rtDispatch.is-ws').count() == 0, 'до сборки плана — страница подготовки')

            built = page.request.post(f'{BASE}/api/routes/dispatch/build', data={'date': DAY, 'trucks': [t['car_code'] for t in day()['trucks']]})
            assert built.ok, built.text()
            edit(action='approve')
            edit(action='unapprove')       # как в живом плане 10.11: отправлен, утверждение снято, правки копятся
            before = day()
            stops = [s for t in before['plan']['trucks'] for tr in t['trips'] for s in tr['stops']]
            check(before.get('sent') and stops, f'план отправлен, магазинов: {len(stops)}')
            for cid in sorted({s['customer_id'] for s in stops}):
                edit(action='defer_store', customer_id=cid, to=None)
            reload_ws()
            after = day()
            check(after['plan'] and not after['plan']['trucks'] and not after['is_past'], 'A завтра, в плане нет рейсов')

            # A
            check(page.locator('#rtDispatch.is-ws').count() == 1 and page.locator('#dpWs').is_visible(), 'A рабочий экран')
            check(page.locator('#dpWs #dpMapBox').is_visible(), 'A карта на месте')
            check(page.locator('#dpViewList').count() == 0 and page.locator('#dpViewWs').count() == 0, 'A переключателей вида нет')
            chip = page.locator('#dpTodoChip')
            check(chip.is_visible() and 'Վերակազմել' in chip.inner_text(), 'A чип «Վերակազմել երթերը»')
            check(page.locator('#dpStoreBar').is_visible(), 'A полоса магазинов видна')
            check(page.locator('#dpSendState .dp-sendpill.is-unsent').is_visible() and page.locator('#dpSendBtn').is_visible(), 'A неотправленное и «Ուղարկել» видны')
            g = page.evaluate(GEOM)
            check(g['side'] <= 0 and g['wsBottom'] <= g['win'] + 1, f'A экран в окне, вбок не уходит {g}')
            if shots:
                page.screenshot(path=str(shots / 'empty-1354.png'))

            # B
            check(page.locator('#dpWizardStart').is_enabled(), 'B кнопка подготовки доступна')
            page.locator('#dpWizardStart').click()
            page.wait_for_selector('#dpWizard[open]', timeout=10000)
            check(page.locator('#dpWizard').is_visible(), 'B пошаговая подготовка открылась')

            # C
            mods['unassigned'] = stops
            reload_ws()
            cell = page.locator('#dpStoreBar button.dp-stcell', has_text='Երթերում չեն')
            check(cell.count() == 1 and str(len(stops)) in cell.inner_text(), 'C «Երթերում չեն» в полосе, число верное')
            cell.click()
            page.wait_for_timeout(700)
            check(page.locator('#dpUnassigned').is_visible() and page.locator('#dpUnassigned .dp-stop').count() >= 1, 'C список магазинов вне рейсов открылся')
            if shots:
                page.screenshot(path=str(shots / 'empty-unassigned.png'))
            mods['unassigned'] = None

            # D
            mods['no_trucks'] = True
            reload_ws()
            check('երթում են' not in page.locator('#dpWsFree').inner_text(), 'D машин дня нет — не пишем, что все в рейсах')
            mods['no_trucks'] = False

            # E
            page.evaluate("localStorage.setItem('dpLayout', 'list')")
            reload_ws()
            check(page.locator('#rtDispatch.is-ws').count() == 1, 'E старый выбор списка не действует')

            # F
            page.set_viewport_size({'width': 900, 'height': 860})
            page.wait_for_timeout(500)
            check(page.locator('#rtDispatch.is-ws').count() == 0 and page.locator('#dpTodo').is_visible() and page.locator('#dpDiscardBtn').is_visible(),
                  'F узкое окно: без рабочего экрана, подсказка и «Վերադարձնել» видны')
            page.set_viewport_size({'width': 1354, 'height': 860})
            page.wait_for_selector('#rtDispatch.is-ws', timeout=10000)

            # G
            page.click('#dpWsMore')
            page.click('#dpMenuDiscard')
            page.click('#dpConfirmOk')
            page.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            page.wait_for_function("() => { const b = document.getElementById('dpBoard'); return b && !b.hidden; }", timeout=15000)
            check(day()['plan']['trucks'] and page.locator('#rtDispatch.is-ws').count() == 1 and page.locator('#dpBoard').is_visible(), 'G рейсы вернулись, экран рабочий')
            if shots:
                page.screenshot(path=str(shots / 'restored-1354.png'))
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERR  ' + e)
    check(not errors, 'нет ошибок страницы и консоли')
    print(f'{sum(results)}/{len(results)}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

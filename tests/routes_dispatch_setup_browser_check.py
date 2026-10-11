# -*- coding: utf-8 -*-
"""Проверка экрана «Առաքում» до сборки рейсов (ответ владельца №99: «Эта страница выглядит очень плохо») в браузере.

Запуск из корня проекта:  python tests/routes_dispatch_setup_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_dispatch_browser_check.py (настоящие шаблон, статика и blueprint, поддельная ERP, временная база), но день —
как у владельца 13.10: заказов дня нет (единственный заказ — менеджера, снятого правилом настроек), «Նախորդ օրերից»
есть; 11 машин и ещё одна с неполными данными; 7 водителей, 6 из них не работают до даты, 5 машин без водителя, один
водитель — у машины, которой нет в списке (выключена в настройках). Порт 8795 на 127.0.0.1.

A одна панель дня: в ней #dpBuild, AI и помощь; «Ի՞նչ անել հիմա», строки «վարորդ նշված չէ» и «Կազմել երթերը» внизу
  страницы нет; плавающей кнопки AI нет (AI — в панели и открывает чат);
B статус: заголовок «Այս օրվա համար պատվերներ դեռ չկան», вывод «կարող է դուրս գալ 1 մեքենա․ եկել է 7 վարորդից 1-ը»,
  чипы с числами: BACKLOG заказов прошлых дней / 6 не работают / 5 без водителя / 1 неполная; клик по «նախորդ օրերից» —
  окно «Նախորդ օրերից», по «առանց վարորդի» — диалог «Վարորդ» первой такой машины, по «չի աշխատում» — фильтр «Խնդիր»;
C таблица «Մեքենաներ և վարորդներ»: 12 строк (11 + неполная последней строкой-ошибкой), счётчик «1 / 11», водитель вне
  списка — в подвале; «Պատրաստ» — 1 строка, «Խնդիր» — 11 (сегмент); «եկել է» → диалог «Վարորդը չի եկել», «Չեղարկել» —
  по-прежнему вышел; «մինչև …» → вернулся (2 / 11) и снова до даты (1 / 11); «+ Նշել վարորդ» → диалог «Վարորդ» этой
  машины; сняли машину вышедшего водителя — он свободен и сядет на другую (по-прежнему 1 / 11, как dispatch.seating),
  сняли все — «0 / 11» и «կարող է դուրս գալ 0»;
D тексты: «Երթերը կազմված են» нигде нет, пока плана нет; про менеджеров — «Կարգավորումների կանոնով», без
  «Նշեք գոնե մեկ մենեջեր» и «Տանում ենք միայն 0»;
E 1366×768, 1920×1080, 390×860: нет горизонтальной прокрутки, фон страницы тёмный до низа документа;
ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов).
"""
from __future__ import annotations

import dataclasses
import logging
import sys
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import erp, views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, make_snapshot  # noqa: E402

PORT = 8795
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
NOW = datetime(2026, 9, 30, 18, 0)
# машина → (марка ERP, кг, въезд в центр)
FLEET = {'333DN33': ('FORD', 2800, False), '123AV61': ('HOWO SIN0TRUK', 5000, False), '306CU61': ('FORD', 2700, False),
         '333DO33': ('FORD', 2500, False), '504 CQ 61': ('FORD', 2500, False), '991AT61': ('HOWO', 5000, False),
         '124AV61': ('HOWO SIN0TRUK', 5000, False), '333NO33': ('FORD', 2500, False), '397HI61': ('JAC', 2200, True),
         '475DD61': ('JAC', 2200, True), '940DE61': ('FORD', 2500, False)}
OFF_TRUCK = '2660062'          # выключена в настройках — её водителя нет в таблице, он в подвале
BROKEN = '1'                   # дубль без тоннажа и расхода — «լրացրեք կարգավորումներում»
# водитель → (машина, по какой день не работает или None — вышел, источник записи)
DRIVERS = {'Հարությունյան Արմեն': ('333DN33', None, 'apk:7'), 'Sergey': ('123AV61', '2026-10-05', 'apk:3'),
           'Գասպարյան Դավիթ': ('306CU61', '2026-10-05', 'qa'), 'Հակոբյան Կարապետ': ('333DO33', '2026-10-12', 'erp:auto'),
           'Թովմասյան Հարություն': ('504 CQ 61', '2026-10-05', 'erp:auto'),
           'Իսկանդարյան Կորյուն': ('991AT61', '2026-10-05', 'erp:auto'),
           'Համբարձումյան Աղվան': (OFF_TRUCK, '2026-10-05', 'qa')}
BACKLOG = 3


def build(tmp: str):
    """Стенд «как у владельца 13.10» (см. docstring); views._clock и _yerevan_now — накануне DAY."""
    app = base.build_app(tmp, base.FakeClient())
    state = app.extensions['route_optimizer']
    snap = make_snapshot()
    cars = {code: erp.Car(code, name, False) for code, (name, _, _) in FLEET.items()}
    cars[OFF_TRUCK] = erp.Car(OFF_TRUCK, 'FORD', False)
    cars[BROKEN] = erp.Car(BROKEN, '', False)
    state.driver_list_loader = lambda since, until: []
    state.crew_days_loader = None
    # заказ дня — только менеджера 2, снятого правилом настроек; прошлых дней — менеджера 1 («Նախորդ օրերից»)
    orders = [_dorder(1, 102, 40.0, agent=2)] + [_dorder(10 + i, 101, 120.0, day=date(2026, 9, 28 + i % 2)) for i in range(BACKLOG)]
    with app.test_request_context():
        class _C:
            application = app
            post = staticmethod(lambda url, json=None: app.test_client().post(url, json=json))
        _dispatch_setup(_C, orders)
    state.snapshots = SnapshotCache(lambda: dataclasses.replace(snap, cars=cars))   # машины ERP — как у владельца
    c = app.test_client()
    trucks = [{'car_code': code, 'capacity_kg': kg, 'fuel_l_per_100km': 18, 'active': True, 'center_ok': center}
              for code, (_, kg, center) in FLEET.items()]
    trucks += [{'car_code': OFF_TRUCK, 'capacity_kg': 2500, 'fuel_l_per_100km': 16, 'active': False},
               {'car_code': BROKEN, 'active': True}]
    r = c.post('/api/routes/settings', json={'trucks': trucks, 'settings': {'dispatch_agents_off': [2]}})
    assert r.status_code == 200, r.get_json()
    for name, (car, until, by) in DRIVERS.items():
        state.store.save_truck_driver(car, DAY, name, by)
        if until:
            state.store.save_driver_absence(name, DAY, until, 'qa')
    views._clock = lambda: NOW
    views._yerevan_now = lambda: NOW.replace(tzinfo=ZoneInfo('Asia/Yerevan'))
    return app


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build(tempfile.mkdtemp(prefix='setup-check-'))
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            def open_page(w, h):
                ctx = browser.new_context(viewport={'width': w, 'height': h})
                page = ctx.new_page()
                page.on('pageerror', lambda e: errors.append(f'pageerror {w}: {e}'))
                page.on('console', lambda m: errors.append(f'console {w}: ' + m.text)
                        if m.type == 'error' and not base.is_ignorable(m) else None)
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
                page.goto(f'{BASE}/routes/dispatch?date={DAY}')
                page.wait_for_selector('#dpBody', state='visible', timeout=30000)
                page.wait_for_selector('#dpFleetRows tr', timeout=15000)
                page.wait_for_timeout(300)
                return page

            page = open_page(1366, 768)
            vis = page.is_visible
            body = lambda: page.inner_text('#rtDispatch')  # noqa: E731

            # A
            check(page.locator('.dp-dayboard #dpBuild').count() == 1 and vis('#dpBuild') and vis('#dpSetAi') and vis('#dpHelpBtn'),
                  'A one toolbar: build, AI and help in it')
            check(not vis('.dp-todo-k') and not vis('#dpNoDriver') and not vis('#dpCrew') and not vis('#dpAiOpen')
                  and page.locator('#dpTodo.is-setup').count() == 1, 'A no «Ի՞նչ անել հիմա», no-driver strip, crew list, floating AI')
            page.click('#dpHelpBtn')
            check(vis('#dpHelp') and page.get_attribute('#dpHelpBtn', 'aria-expanded') == 'true', 'A help opens under the toolbar')
            page.click('#dpHelpBtn')
            check(not vis('#dpHelp'), 'A help closes')
            page.click('#dpSetAi')
            check(vis('#dpAi'), 'A AI button opens the chat')
            page.click('#dpAiClose')

            # B
            todo = page.inner_text('#dpTodo')
            check('Այս օրվա համար պատվերներ դեռ չկան' in todo and 'կարող է դուրս գալ 1 մեքենա' in todo
                  and 'եկել է 7 վարորդից 1-ը' in todo, f'B honest status: {todo!r}')
            chips = {c.get_attribute('data-chip'): ' '.join(c.inner_text().split())
                     for c in page.locator('#dpTodo .dp-chipbtn').all()}
            want = {'backlog': f'{BACKLOG} պատվեր նախորդ օրերից', 'absent': '6 վարորդ չի աշխատում',
                    'nodriver': '5 մեքենա առանց վարորդի', 'broken': '1 մեքենայի տվյալները թերի են'}
            check(chips == want, f'B chips with numbers: {chips}')
            page.click('#dpTodo [data-chip="backlog"]')
            page.wait_for_selector('#dpBlDlg[open]', timeout=5000)
            check(page.locator('#dpBacklogList li[data-isn]').count() == BACKLOG, 'B backlog chip opens «Նախորդ օրերից»')
            page.click('#dpBlClose')
            page.click('#dpTodo [data-chip="nodriver"]')
            page.wait_for_selector('#dpDriverDlg[open]', timeout=5000)
            check('124AV61' in page.inner_text('#dpDriverLead'), 'B no-driver chip opens the driver dialog of the first such truck')
            page.click('#dpDriverCancel')
            page.click('#dpTodo [data-chip="absent"]')
            check(page.get_attribute('#dpFleetSeg [data-f="bad"]', 'aria-pressed') == 'true'
                  and page.locator('#dpFleetRows tr:visible').count() == 11, 'B absent chip → filter «Խնդիր»')

            # C
            page.click('#dpFleetSeg [data-f="all"]')
            rows = page.locator('#dpFleetRows tr')
            check(rows.count() == 12 and 'is-bad' in (rows.last.get_attribute('class') or '')
                  and 'կարգավորումներում' in rows.last.inner_text(), f'C 12 rows, broken truck last ({rows.count()})')
            check(page.inner_text('#dpTrucksCount').replace(' ', ' ') == 'աշխատում է՝ 1 / 11', 'C counter 1 / 11: '
                  + page.inner_text('#dpTrucksCount'))
            foot = page.inner_text('#dpFleetLost')
            check('Համբարձումյան Աղվան' in foot and OFF_TRUCK in foot, f'C driver of a truck not in the list — footer: {foot!r}')
            page.click('#dpFleetSeg [data-f="ready"]')
            check(page.locator('#dpFleetRows tr:visible').count() == 1, 'C «Պատրաստ» — 1 row')
            page.click('#dpFleetSeg [data-f="all"]')
            armen = rows.filter(has_text='333DN33')
            check('եկել է' in armen.inner_text() and 'APK' in armen.inner_text() and 'ՀԱ' in armen.inner_text(),
                  'C row: truck, driver with initials and APK, «եկել է»')
            armen.locator('.dp-pres input').uncheck()
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            check('Հարությունյան Արմեն' in page.inner_text('#dpAbsentLead'), 'C «եկել է» → «Վարորդը չի եկել» dialog')
            page.click('#dpAbsentCancel')
            page.wait_for_function("() => !document.getElementById('dpAbsentDlg').open", timeout=5000)
            page.wait_for_timeout(100)        # событие close диалога — следующей задачей
            check(armen.locator('.dp-pres input').is_checked(), 'C cancel — still came')
            sergey = rows.filter(has_text='123AV61')
            check('մինչև 05.10' in sergey.inner_text(), 'C absent driver: «մինչև 05.10»')
            sergey.locator('.dp-pres input').check()
            page.wait_for_function("() => document.getElementById('dpTrucksCount').textContent.includes('2 / 11')", timeout=10000)
            check('եկել է' in rows.filter(has_text='123AV61').inner_text(), 'C «մինչև» → returned, counter 2 / 11')
            rows.filter(has_text='123AV61').locator('.dp-pres input').uncheck()
            page.wait_for_selector('#dpAbsentDlg[open]', timeout=5000)
            page.fill('#dpAbsentUntil', '2026-10-05')
            page.click('#dpAbsentSave')
            page.wait_for_function("() => document.getElementById('dpTrucksCount').textContent.includes('1 / 11')", timeout=10000)
            rows.filter(has_text='940DE61').locator('.dp-setdrv').click()
            page.wait_for_selector('#dpDriverDlg[open]', timeout=5000)
            check('940DE61' in page.inner_text('#dpDriverLead'), 'C «+ Նշել վարորդ» → driver dialog of that truck')
            page.click('#dpDriverCancel')
            # его машину сняли — он свободен и сядет на машину невышедшего (dispatch.seating): по-прежнему 1
            armen.locator('input.dp-tkpick').uncheck()
            check('is-off' in (armen.get_attribute('class') or '') and page.inner_text('#dpTrucksCount').endswith('1 / 11'),
                  'C own truck unticked — the driver is free for another truck, still 1 / 11')
            picks = page.locator('#dpFleetRows input.dp-tkpick:checked')
            while picks.count():
                picks.first.uncheck()
            check(page.inner_text('#dpTrucksCount').replace(' ', ' ') == 'աշխատում է՝ 0 / 11'
                  and 'կարող է դուրս գալ 0' in page.inner_text('#dpTodo'), 'C all trucks unticked — 0 / 11')
            for cb in page.locator('#dpFleetRows input.dp-tkpick:not(:disabled)').all():
                cb.check()

            # D
            text = body()
            check('Երթերը կազմված են' not in text and 'Երթերը կազմված են' not in page.text_content('body'),
                  'D no «plan built» text without a plan')
            check('Կարգավորումների կանոնով' in text and 'Նշեք գոնե մեկ մենեջեր' not in page.text_content('body')
                  and 'Տանում ենք միայն 0' not in page.text_content('body'), 'D managers: rule of the settings, no «tick a manager»')

            # E
            def fits(pg, tag):
                sw = pg.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
                bg = pg.evaluate("getComputedStyle(document.body).backgroundColor")
                check(sw and bg in ('rgb(14, 17, 22)',), f'E {tag}: no horizontal scroll ({sw}), dark background to the bottom ({bg})')
            fits(page, '1366×768')
            page.context.close()
            for w, h in ((1920, 1080), (390, 860)):
                pg = open_page(w, h)
                fits(pg, f'{w}×{h}')
                pg.context.close()

            check(not errors, 'no pageerror / console errors' + ('' if not errors else ': ' + ' | '.join(errors[:5])))
            browser.close()
    finally:
        server.shutdown()
    print(f'\n{sum(results)}/{len(results)} OK')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

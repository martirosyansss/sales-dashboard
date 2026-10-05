# -*- coding: utf-8 -*-
"""Правила «чьи заказы везём» (№74) в настоящем браузере: настройки «Маршрутов» и страница «Развоз».

Запуск из корня проекта:  python tests/routes_dispatch_settings_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение — как в
tests/routes_dispatch_browser_check.py, заказы — как в tests/test_route_dispatch_settings.py («Rocarm» — менеджер 2, его
заказы «ինքն է տանում»: Ереван 104, Капан 105, Гюмри в названии 107; 108 — внутренний счёт; 109 — менеджер 3 везёт сам).
Поиск клиентов и подсказка ERP — заглушки. Порт 8769 на 127.0.0.1.

S1 настройки: карточки «Մենեջերներ, որոնց «ինքն է տանում»…» и «Հաճախորդներ, որոնց…» видны; два менеджера без отметок,
   города по умолчанию, состояние «կանոն չկա» / «տանում ենք բոլորին»;
S2 отметить менеджера 2, убрать Գորիս — форма изменена, состояние «մենեջեր՝ 1 · բացառություն՝ 3 քաղաք»;
S3 поиск «7092» → строка с «Ավելացնել»; добавить — в списке, кнопка «Ավելացված է» и неактивна;
S4 «Ցույց տալ» — подсказка ERP: причины «հասցե չկա» / «Հայաստանից դուրս», менеджер, заказы; добавить «ИП Магдасян»;
S5 «Հանել» у 7092 — его нет в списке, кнопка поиска снова «Ավելացնել»;
S6 «Պահպանել» → dispatch_fleet_agents [2], dispatch_other_cities без Գորիս, dispatch_customers_off [67495];
S7 перезагрузка: в списке название клиента (из ERP по id), а не «հաճախորդ #…»;
P  телефон 390×860: на настройках нет горизонтальной прокрутки;
D1 «Развоз»: «Առաքման մեջ չեն մտնում՝ … գնում է այլ մեքենայով, … չենք տանում՝ կարգավորումներով», заказов в развозе 4;
D2 №72 L2: менеджер 3, у которого сегодня только новый заказ, — в фильтре «Մենեջերներ» с «այսօրվա նոր՝ 1 պատվեր».
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp, views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from test_route_optimizer import _dorder  # noqa: E402

PORT = 8769
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-settings-check'
R = 2
ORDERS = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=R), _dorder(5, 104, 100.0, agent=R, van=R),
          _dorder(7, 105, 200.0, agent=R, van=R), _dorder(8, 107, 150.0, agent=R, van=R), _dorder(9, 108, 50.0),
          _dorder(10, 109, 70.0, agent=3, van=3), _dorder(11, 105, 60.0)]
NAMES = {101: ('C101', 'Клиент 101'), 102: ('C102', 'Клиент 102'), 104: ('C104', 'Клиент 104'),
         105: ('C105', 'Արա Աբգարյան ԱՁ'), 107: ('C107', 'Տռովիքս ՍՊԸ/ԳՅՈՒՄՐԻ'), 108: ('7092', 'Վարչական/ ռոքարմ'),
         109: ('C109', 'Клиент 109')}
ADDRESSES = {101: 'Ереван, 1', 104: 'Երևան, Աջափնյակ', 105: 'ՍՅՈՒՆԻՔ, ԿԱՊԱՆ,Լեռնագործների 4'}
REFS = {108: erp.CustomerRef(108, '7092', 'Վարչական/ ռոքարմ', ''),
        67495: erp.CustomerRef(67495, '12300', 'ИП Магдасян Арсена Альбетожна', 'г. Краснодар,ул. Ставраполская 125')}
HINTS = [erp.CustomerHint(67495, '12300', 'ИП Магдасян Арсена Альбетожна', 'г. Краснодар,ул. Ставраполская 125',
                          dp.ABROAD, 4, 18044999.0, date(2026, 9, 25), (1,)),
         erp.CustomerHint(108, '7092', 'Վարչական/ ռոքարմ', '', dp.NO_ADDRESS, 31, 271076.0, date(2026, 10, 1), (1, 2))]


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-settings-'), FakeClient())
    state = app.extensions['route_optimizer']
    data = state.dispatch_loader(None, None, None)
    state.dispatch_loader = lambda since, until, day: replace(data, orders=tuple(ORDERS), customers=NAMES,
                                                              addresses=ADDRESSES)
    state.customer_ref_loader = lambda q, ids: ([REFS[108]] if q.strip() == '7092' else []) if q.strip() \
        else [REFS[i] for i in ids if i in REFS]
    state.customer_hint_loader = lambda since, until: HINTS
    # №72 L2: «сейчас» — 01.10 08:00 по Еревану; менеджер 3 принял сегодня только новый заказ
    views._yerevan_now = lambda: datetime(2026, 10, 1, 8, 0, tzinfo=YEREVAN)
    today = [_dorder(20, 103, 70.0, day=date(2026, 10, 1), agent=3)]
    state.same_day_loader = lambda day: dp.SameDayData(tuple(today), {}, {103: ('C103', 'Клиент 103')}, {},
                                                       datetime(2026, 10, 1, 8, 0))
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            saved = lambda: page.evaluate("() => fetch('/api/routes/settings').then(r => r.json())")['settings']  # noqa: E731

            # S1
            page.goto(f'{BASE}/routes/settings#fleet')
            page.wait_for_selector('#rsFleetList', state='visible', timeout=30000)
            fleet = page.locator('#rsFleetList input[type="checkbox"]')
            check(page.locator('#fleet').is_visible() and page.locator('#custoff').is_visible(), 'S1 both rule cards visible')
            check(fleet.count() == 2 and not any(fleet.nth(i).is_checked() for i in range(2)), 'S1 two managers, none checked')
            check(page.locator('#rsCities').input_value() == 'Գյումրի, Կապան, Գորիս, Վանաձոր', 'S1 default cities')
            check(page.locator('#rsStFleet').inner_text().strip() == 'կանոն չկա'
                  and page.locator('#rsStCustOff').inner_text().strip() == 'տանում ենք բոլորին', 'S1 card states')

            # S2
            page.locator('#rsFleetList input[value="2"]').check()
            page.locator('#rsCities').fill('Գյումրի, Կապան, Վանաձոր')
            st = page.locator('#rsStFleet').inner_text()
            check('մենեջեր՝ 1' in st and '3' in st and 'is-dirty' in page.locator('#rsDirty').get_attribute('class'),
                  'S2 state and dirty: ' + st)

            # S3
            page.locator('#rsCustQ').fill('7092')
            page.wait_for_selector('#rsCustFound [data-add-customer="108"]', timeout=10000)
            page.locator('#rsCustFound [data-add-customer="108"]').click()
            check(page.locator('#rsCustOffList [data-customer-id="108"]').count() == 1, 'S3 added 7092 to the list')
            b = page.locator('#rsCustFound [data-add-customer="108"]')
            check(b.is_disabled() and b.inner_text() == 'Ավելացված է', 'S3 search button «Ավելացված է», disabled')

            # S4
            page.locator('#rsCustHintsBtn').click()
            page.wait_for_selector('#rsCustHints [data-add-customer="67495"]', timeout=10000)
            hints = page.locator('#rsCustHints').inner_text()
            check('Հայաստանից դուրս' in hints and 'հասցե չկա' in hints and 'Менеджер 1' in hints and '31' in hints,
                  'S4 hints show reasons, manager, orders')
            check(page.locator('#rsCustHints [data-add-customer="108"]').is_disabled(), 'S4 already-added hint disabled')
            page.locator('#rsCustHints [data-add-customer="67495"]').click()
            check(page.locator('#rsCustOffList [data-customer-id="67495"]').count() == 1, 'S4 added from hints')
            page.screenshot(path=str(SHOTS / 's4-settings.png'), full_page=False)

            # S5
            page.locator('#rsCustOffList [data-customer-id="108"] button').click()
            check(page.locator('#rsCustOffList [data-customer-id="108"]').count() == 0
                  and page.locator('#rsCustFound [data-add-customer="108"]').inner_text() == 'Ավելացնել', 'S5 removed, re-addable')

            # S6
            page.locator('#rsSaveBtn').click()
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            s = saved()
            check(s['dispatch_fleet_agents'] == [2] and s['dispatch_other_cities'] == ['Գյումրի', 'Կապան', 'Վանաձոր']
                  and s['dispatch_customers_off'] == [67495], 'S6 saved: ' + str({k: s[k] for k in (
                      'dispatch_fleet_agents', 'dispatch_other_cities', 'dispatch_customers_off')}))

            # S7
            page.reload()
            page.wait_for_selector('#rsCustOffList [data-customer-id="67495"]', timeout=30000)
            page.wait_for_function("() => document.querySelector('#rsCustOffList [data-customer-id=\"67495\"]').textContent"
                                   ".includes('Магдасян')", timeout=10000)
            check('12300' in page.locator('#rsCustOffList').inner_text(), 'S7 name of saved customer loaded by id')
            check(page.locator('#rsDirty').inner_text() == 'Փոփոխություններ չկան', 'S7 form clean after reload')

            # P
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(over <= 0, f'P phone settings: no horizontal scroll ({over}px)')
            page.locator('#custoff').screenshot(path=str(SHOTS / 'p-phone-custoff.png'))
            page.set_viewport_size({'width': 1440, 'height': 950})

            # D1 — правило: 108 снова «не везём» (настройку меняем запросом, как владелец)
            page.evaluate("() => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                          " body: JSON.stringify({settings: {dispatch_customers_off: [108], dispatch_other_cities:"
                          " ['Գյումրի', 'Կապան']}})})")
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            info = page.locator('#dpOrdersInfo').inner_text()
            check('2' in info and 'գնում է այլ մեքենայով' in info and 'չենք տանում՝ կարգավորումներով' in info
                  and 'մենեջերն ինքն է տանում' in info, 'D1 counters: ' + info)
            d = page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")
            check(d['orders']['count'] == 4 and d['orders']['other_vehicle'] == 2, 'D1 orders in dispatch 4, other vehicle 2')

            # D2
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            lab = page.locator('#dpAgentsList label', has=page.locator('input[value="3"]'))
            check(lab.count() == 1 and 'այսօրվա նոր՝ 1' in lab.inner_text(), 'D2 manager 3 listed with new order of today: '
                  + (lab.inner_text() if lab.count() else '-'))
            page.screenshot(path=str(SHOTS / 'd-dispatch.png'), full_page=False)
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

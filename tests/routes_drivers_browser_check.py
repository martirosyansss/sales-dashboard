# -*- coding: utf-8 -*-
"""Проверка «Վարորդներ» (/routes/drivers, №83 и №87 п. 3 и 7) и плиток ETA на «Ուսուցում» в настоящем браузере.

Запуск из корня проекта:  python tests/routes_drivers_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_dispatch_browser_check.py (настоящие шаблоны, статика и blueprint, поддельная ERP), всё во временной
папке; роль запроса (admin / garage) задаёт сама проверка — гейт входа app_v2 здесь не участвует (его проверяют
тесты tests/test_route_driver_scorecard.py). Факт «Առաքիչ» и GPS — синтетические (как в тестах): 02–05.10 CAR1
(Արամ + առաքիչ Բաբկեն: превышение скорости, стоянка 25 мин вне магазинов, B на 40 мин позже плана) и CAR2 (Գոռ, всё
по плану); Դավիթ — 2 дня на CAR3 без GPS («քիչ տվյալ»). «Сейчас» — 07.10.2026 12:00. Порт 8774 на 127.0.0.1.

A администратор, 1440×900: плитки парка и ETA, вкладки раздела, столбец «Կանխիկ», балл и место у Արամ и Գոռ,
  «քիչ տվյալ» у Դավիթ, առաքիչ без балла; по умолчанию — сортировка по баллу (лучший сверху);
B строка Արամ раскрывается: разбивка балла (полоски, доли) и дни с превышением, стоянкой и опоздавшим магазином;
  подсказка балла — составляющие;
C сортировка по «Արագություն»: меньше превышений — выше;
D «Гараж»: вкладок нет, есть «Ավտոտնակ», столбца «Կանխիկ» нет ни в шапке, ни в строках, ни в разбивке по дням; в
  ответе API — cash: false;
E телефон 390×860: у страницы нет горизонтальной прокрутки (таблица прокручивается внутри своей рамки);
F «Ուսուցում» за 02–05.10: ряд плиток точности ETA виден и согласован с «Վարորդներ».
Ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов — шрифты, CDN).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
from flask import g  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.actuals import YEREVAN  # noqa: E402
from test_route_live import Track  # noqa: E402

PORT = 8774
BASE = f'http://127.0.0.1:{PORT}'
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=YEREVAN)
DAYS = [date(2026, 10, 2) + timedelta(days=i) for i in range(4)]
A, B, C = (40.1700, 44.4700), (40.1800, 44.4900), (40.1600, 44.5100)
E, F = (40.1900, 44.4600), (40.2000, 44.4800)
X = (40.1750, 44.5050)   # не магазин и не склад
NAMES = {1: 'Արամ', 2: 'Բաբկեն', 3: 'Գոռ', 4: 'Դավիթ'}


class FakeCrew:
    def __init__(self, data):
        self.data = data

    def versions(self, since, until):
        return {d: (1,) for d in self.data if since <= d <= until}

    def day(self, ds):
        return self.data[ds]

    def names(self):
        return dict(NAMES)


class FakeFleet:
    def __init__(self, days):
        self.days = days

    def car_days(self, since, until):
        return sorted((c, d) for c, d in self.days if since <= d <= until)

    def version(self, car, ds):
        return (len(self.days[(car, ds)]['track']),)

    def day(self, car, ds):
        return self.days[(car, ds)]

    def refuels(self, since=''):
        return []


def _fstop(sid, cid, p, seq):
    return {'stop_id': sid, 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': 100.0, 'seq': seq,
            'delivered_share': 1.0, 'delivered_at': None}


def _cstop(sid, car, status, driver, helper=None, cid=None):
    return {'stop_id': sid, 'car_code': car, 'status': status, 'driver_id': driver, 'helper_id': helper,
            'customer_id': cid, 'name': f'Խանութ {cid}'}


def seed(app) -> None:
    """Факт и планы 02–05.10 (см. описание модуля) в состояние «Маршрутов» тестового приложения."""
    state = app.extensions['route_optimizer']
    depot = state.store.load().depot or (40.1500, 44.4500)
    hm = lambda t: t.strftime('%H:%M')   # noqa: E731
    crew, fleet = {}, {}
    for i, day in enumerate(DAYS):
        ds = day.isoformat()
        start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=YEREVAN)
        t1 = Track(start).park(depot, 10).drive(A)
        at_a = t1.t
        t1.park(A, 8).drive(B, speed_ms=30.0 if i % 2 == 0 else 10.0)
        at_b = t1.t
        t1.park(B, 8)
        if i == 0:
            t1.drive(X).park(X, 25)
        t1.drive(C).park(C, 8).drive(depot).park(depot, 3)
        t2 = Track(start).park(depot, 5).drive(E)
        at_e = t2.t
        t2.park(E, 8).drive(F)
        at_f = t2.t
        t2.park(F, 8).drive(depot).park(depot, 3)
        fleet[('CAR1', ds)] = {'track': t1.pts, 'stops': [_fstop(f'S:{ds}:A', 11, A, 1), _fstop(f'S:{ds}:B', 12, B, 2),
                                                          _fstop(f'S:{ds}:C', 13, C, 3)]}
        fleet[('CAR2', ds)] = {'track': t2.pts, 'stops': [_fstop(f'S:{ds}:E', 21, E, 1), _fstop(f'S:{ds}:F', 22, F, 2)]}
        stops = [_cstop(f'S:{ds}:A', 'CAR1', 'full', 1, 2, 11), _cstop(f'S:{ds}:B', 'CAR1', 'partial', 1, 2, 12),
                 _cstop(f'S:{ds}:C', 'CAR1', 'full', 1, None, 13),
                 _cstop(f'S:{ds}:E', 'CAR2', 'full', 3, None, 21), _cstop(f'S:{ds}:F', 'CAR2', 'full', 3, None, 22)]
        if i < 2:
            stops.append(_cstop(f'S:{ds}:G', 'CAR3', 'full', 4, None, 31))
        crew[ds] = {'stops': stops,
                    'cash': {1: {'expected': 1000.0, 'short': 200.0, 'collected': 800.0, 'handed': None, 'diff': None}},
                    'tare': {1: 2.0}}
        draft = dp.Draft(trucks=['CAR1', 'CAR2'], trips=[dp.DraftTrip(1, 'CAR1', [11, 12]), dp.DraftTrip(2, 'CAR2', [21, 22])])
        draft.prediction = {'trucks': {
            'CAR1': {'trips': [{'depart': '09:10', 'stops': [[11, hm(at_a - timedelta(minutes=5))],
                                                             [12, hm(at_b - timedelta(minutes=40))]]}]},
            'CAR2': {'trips': [{'depart': '09:05', 'stops': [[21, hm(at_e + timedelta(minutes=2))],
                                                             [22, hm(at_f - timedelta(minutes=3))]]}]}}}
        state.store.save_dispatch(ds, draft.to_json(), 'qa')
    state.crew_facts = FakeCrew(crew)
    state.fleet_facts = FakeFleet(fleet)


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')    # консоль Windows (cp1252) не печатает армянский
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    tmp = tempfile.mkdtemp(prefix='drivers-check-')
    app = base.build_app(tmp, base.FakeClient())
    role = {'name': 'admin'}
    # роль — как ставит гейт app_v2 (приложение уже отвечало на запросы при сборке: регистрируем напрямую)
    app.before_request_funcs.setdefault(None, []).append(lambda: setattr(g, 'user_role', role['name']))
    app.template_context_processors[None].append(lambda: {
        'is_admin': role['name'] == 'admin', 'is_garage': role['name'] == 'garage',
        'is_public_role': role['name'] == 'garage'})
    views._yerevan_now = lambda: NOW
    seed(app)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, bodies = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text)
                    if m.type == 'error' and not base.is_ignorable(m) else None)
            page.on('response', lambda r: bodies.append(r.json()) if '/api/routes/drivers/scorecard' in r.url else None)
            rows = lambda: page.eval_on_selector_all(   # noqa: E731
                '#drRows tr.dr-row', 'els => els.map(e => [e.querySelector(".dr-name span").textContent, '
                'e.querySelector(".dr-score").textContent])')

            # A администратор
            page.goto(f'{BASE}/routes/drivers')
            page.wait_for_selector('#drRows tr.dr-row', timeout=30000)
            got = rows()
            check([n for n, _ in got][:2] == ['Գոռ', 'Արամ'], f'A по баллу: Գոռ (всё по плану) выше Արամ — {got}')
            score = dict(got)
            check(score.get('Դավիթ') == 'քիչ տվյալ' and score.get('Բաբկեն') == '—',
                  'A Դավիթ (2 дня) — «քիչ տվյալ», առաքիչ — без балла')
            check(score.get('Գոռ', '').endswith('1-ին 2-ից') and score.get('Արամ', '').endswith('2-րդ 2-ից'),
                  f'A место среди двух с баллом (1-ին, 2-րդ) — {score}')
            check(page.locator('#drTable thead th[data-key="cash"]').count() == 1
                  and page.locator('.rt-tabs').count() == 1, 'A у администратора — «Կանխիկ» и вкладки раздела')
            check(page.locator('#drKpis .dr-kpi').count() == 4 and page.is_visible('#drEtaBox')
                  and page.locator('#drEta .dr-kpi').count() == 4, 'A плитки парка и 4 плитки точности ETA')
            eta = bodies[-1]['eta']
            check(eta['n'] == 16 and page.inner_text('#drEta').count('%') >= 3,
                  f'A ETA по 16 точкам (A, B, E, F × 4 дня) — {eta}')

            # B разбивка
            aram = page.locator('#drRows tr.dr-row', has_text='Արամ')
            title = aram.locator('.dr-score').get_attribute('title') or ''
            check('Արագություն' in title and 'Ժամանակին' in title and '×' in title, 'B подсказка балла — составляющие')
            aram.click()
            detail = page.locator('#drRows tr.dr-detail:not([hidden])')
            check(detail.count() == 1 and detail.locator('.dr-parts li').count() == 4
                  and detail.locator('.dr-parts .bar i').count() == 4, 'B раскрыто: 4 составляющие с полосками')
            text = detail.inner_text()
            check('անգամ' in text and 'պլանից ուշ' in text and detail.locator('.dr-days tbody tr').count() == 4,
                  'B по дням: превышения, опоздавший магазин, 4 дня')
            check(page.get_attribute('#drRows tr.dr-row.is-open .dr-name', 'aria-expanded') == 'true',
                  'B кнопка имени — aria-expanded')

            # C сортировка по скорости: меньше превышений — выше
            page.click('#drTable th[data-key="speed_per_100km"] .dr-sort')
            check([n for n, _ in rows()][0] == 'Գոռ' and page.get_attribute(
                '#drTable th[data-key="speed_per_100km"]', 'aria-sort') == 'ascending', 'C «Արագություն»: лучший сверху')

            # D «Гараж»
            role['name'] = 'garage'
            page.goto(f'{BASE}/routes/drivers')
            page.wait_for_selector('#drRows tr.dr-row', timeout=30000)
            body = bodies[-1]
            check(body['cash'] is False and all('cash' not in r for r in body['drivers'])
                  and all('cash' not in d for r in body['drivers'] for d in r['detail']), 'D в ответе API денег нет')
            check(page.locator('#drTable th[data-key="cash"]').count() == 0 and page.locator('.rt-tabs').count() == 0
                  and page.locator('a[href="/routes/garage"]').count() >= 1, 'D без «Կանխիկ» и вкладок, есть «Ավտոտնակ»')
            page.locator('#drRows tr.dr-row', has_text='Արամ').click()
            heads = page.eval_on_selector_all('#drRows tr.dr-detail:not([hidden]) .dr-days thead th',
                                              'els => els.map(e => e.textContent)')
            cells = page.eval_on_selector('#drRows tr.dr-row', 'e => e.children.length')
            check('Կանխիկ' not in heads and cells == 13 and 'Կանխիկ' not in page.inner_text('#drPage'),
                  f'D нет денег и в разбивке по дням ({cells} ячеек в строке)')

            # E телефон
            phone = browser.new_page(viewport={'width': 390, 'height': 860})
            phone.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            phone.goto(f'{BASE}/routes/drivers')
            phone.wait_for_selector('#drRows tr.dr-row', timeout=30000)
            over = phone.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            inner = phone.evaluate("() => { const s = document.querySelector('.dr-scroll'); return s.scrollWidth > s.clientWidth; }")
            check(over <= 0 and inner, f'E 390 px: странице не нужна горизонтальная прокрутка ({over}), таблице — да')

            # F «Ուսուցում»
            role['name'] = 'admin'
            page.goto(f'{BASE}/routes/learning')
            page.fill('#lrFrom', DAYS[0].isoformat())
            page.fill('#lrTo', DAYS[-1].isoformat())
            page.click('#lrPeriod button[type="submit"]')
            page.wait_for_selector('#lrEta:not([hidden]) .rt-kpi', timeout=30000)
            tiles = page.locator('#lrEta .rt-kpi').count()
            first = page.inner_text('#lrEta .rt-kpi >> nth=0')
            check(tiles == 4 and 'ETA' in first and '%' in first, f'F «Ուսուցում»: 4 плитки ETA — {first!r}')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('FAIL ' + e)
    ok = all(results) and not errors
    print(f'\n{sum(results)}/{len(results)} проверок, ошибок страницы: {len(errors)} — ' + ('ОК' if ok else 'ПРОВАЛ'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())

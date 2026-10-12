# -*- coding: utf-8 -*-
"""Проверка «Աշխատավարձ» (/routes/pay) и «Առաքիչների KPI» (/routes/araqich) в раскладке Tesla+ (фаза 4) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_tables_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — как в
tests/routes_cost_browser_check.py (поддельная ERP, роль администратор); накладные экспедиторов за этот и прошлый месяц —
подменённый state.crew_pay_loader, факт «Առաքիչ» и GPS — из tests/routes_drivers_browser_check.py. Порт 8805.

A «Աշխատավարձ»: шапка страницы (заголовок, строка, «Ինչպե՞ս օգտվել» свёрнут), 4 показателя месяца — те же числа, что
  в строке «Ընդամենը» (Վճարել, առաքիչներ, կետեր, տարբերություն), таблица и итог; формула — под таблицей;
B «Առաքիչների KPI»: шапка страницы, 4 плитки команды выше таблиц, балл по плану и таблица объёма;
C обе страницы в обеих темах на 390 / 1366 / 1920 — без горизонтальной прокрутки (tesla_layout_check).
Ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_cost_browser_check as cost  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
import routes_dispatch_browser_check as base  # noqa: E402
import routes_drivers_browser_check as drv  # noqa: E402
import tesla_layout_check as tl  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import views  # noqa: E402

PORT = 8805
BASE = f'http://127.0.0.1:{PORT}'


def seed_crew(app):
    """Накладные трёх экспедиторов в рабочие дни с начала прошлого месяца по «сегодня» стенда (drv.NOW)."""
    today = drv.NOW.date()
    inv, k = [], 0
    agents = {1: ('A001/4', 'Մ. Պետրոսյան'), 2: ('A002/1', 'Ա. Սարգսյան'),
              11: ('B001/1', 'Կորյուն Ավետիսյան'), 12: ('B001/2', 'Արամ Գասպարյան'), 13: ('B001/3', 'Գոռ Մկրտչյան')}
    d = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    while d <= today:
        if d.weekday() < 6:
            for van, n in ((11, 14), (12, 11), (13, 7)):
                for j in range(n - d.day % 3):
                    k += 1
                    inv.append(cp.Invoice(van, d, 100 + k % 60, 1 + j % 2, 18_000.0 + (k * 37) % 40_000, 60.0 + (k * 13) % 240))
        d += timedelta(days=1)
    state = app.extensions['route_optimizer']
    state.crew_pay_loader = lambda since, until: cp.CrewData(tuple(x for x in inv if since <= x.day < until), agents)
    state.crew_pay_cache = {}


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results, errors = [], []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = cost.build(tempfile.mkdtemp(prefix='tables-check-'))
    views._yerevan_now = lambda: drv.NOW
    drv.seed(app)
    seed_crew(app)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pay_ready = lambda pg: pg.wait_for_selector('#cpRows tr', timeout=60000)   # noqa: E731
    kp_ready = lambda pg: pg.wait_for_selector('#kpTeam .rt-kpi', timeout=60000)   # noqa: E731
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
            page.on('console', lambda m: errors.append(f'console: {m.text}') if m.type == 'error' and not base.is_ignorable(m) else None)

            # A «Աշխատավարձ»
            page.goto(f'{BASE}/routes/pay')
            pay_ready(page)
            a = page.evaluate("""() => {
                const foot = [...document.querySelectorAll('#cpTotals td')].map(td => td.textContent.trim());
                const nums = [...document.querySelectorAll('#cpKpis .rt-kpi__num')].map(e => e.textContent.trim());
                const order = [...document.querySelectorAll('#cpPage > *')].map(e => e.id || e.className);
                return { head: !!document.querySelector('#cpPage header.rt-pagehead h1.rt-pagehead__title'),
                         howto: document.querySelector('#cpPage details.rt-howto').open, foot, nums,
                         rows: document.querySelectorAll('#cpRows tr').length,
                         formulaAfter: order.indexOf('cpFormulaTitle') === -1 &&
                             document.querySelector('#cpFormula').compareDocumentPosition(document.querySelector('#cpRows')) & Node.DOCUMENT_POSITION_PRECEDING }; }""")
            check(a['head'] and a['howto'] is False and a['rows'] == 3, f'A шапка страницы, пояснение свёрнуто, 3 առաքիչ ({a["rows"]})')
            check(len(a['nums']) == 4 and a['nums'][0] == a['foot'][8] and a['nums'][2] == a['foot'][2] and a['nums'][3] == a['foot'][10]
                  and a['nums'][1] == '3', f'A показатели = итог таблицы ({a["nums"]} / {a["foot"]})')
            check(bool(a['formulaAfter']), 'A формула — под таблицей')

            # B «Առաքիչների KPI»
            page.goto(f'{BASE}/routes/araqich')
            kp_ready(page)
            page.wait_for_selector('#kpRows tr', timeout=60000)
            b = page.evaluate("""() => ({ head: !!document.querySelector('#kpPage header.rt-pagehead h1.rt-pagehead__title'),
                tiles: document.querySelectorAll('#kpTeam .rt-kpi').length,
                first: document.querySelector('#kpTeam').compareDocumentPosition(document.querySelector('#kpPlanCard')) & Node.DOCUMENT_POSITION_FOLLOWING,
                rows: document.querySelectorAll('#kpRows tr').length,
                w: getComputedStyle(document.querySelector('#kpTeam .rt-kpi .now')).fontWeight })""")
            check(b['head'] and b['tiles'] == 4 and b['first'] and b['rows'] == 3 and b['w'] == '300',
                  f'B шапка, 4 плитки выше таблиц, 3 строки объёма ({b})')

            # C темы и ширины
            tl.themes_and_widths(browser, f'{BASE}/routes/pay', pay_ready, check, errors, 'C pay')
            tl.themes_and_widths(browser, f'{BASE}/routes/araqich', kp_ready, check, errors, 'C araqich')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERROR ' + e)
    check(not errors, f'ошибок страницы и консоли: {len(errors)}')
    print(f'{sum(results)}/{len(results)} проверок')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

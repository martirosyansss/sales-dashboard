# -*- coding: utf-8 -*-
"""Блок «Telegram» настроек «Маршрутов» (бот «Araqich Dispatch», №91) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_tg_settings_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и заглушка ERP — как в
tests/routes_dispatch_browser_check.py; база маршрутов — временная. Порт 8773 на 127.0.0.1.

T1 по умолчанию: уровни ответа владельца (Կապ չկա 🔴, но SIM не отмечен), эскалация 10 мин владельцу 838786551, все
   отчёты включены, итог не позже 19:30;
T2 SIM отмечен, «скорость» — 🔴, эскалация 15 мин двум людям, недельный рейтинг выключен, итог 18:45 → форма изменена,
   «Պահպանել» → на сервере то же;
T3 перезагрузка: поля показывают сохранённое, форма чистая;
T4 id «abc» — ошибка у поля, ничего не сохранено; эскалация 241 — ошибка сервера у поля;
P  телефон 390×860: нет горизонтальной прокрутки.
Ошибки страницы и консоли — провал (кроме внешних ресурсов).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))

from routes_dispatch_browser_check import TILE_PNG, FakeClient, build_app, is_ignorable  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8773
BASE = f'http://127.0.0.1:{PORT}'
SHOTS = Path(tempfile.gettempdir()) / 'tg-settings-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='tg-settings-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) and '400 (BAD REQUEST)' not in m.text else None)   # 400 — ожидаемый отказ T4
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            saved = lambda: page.evaluate("() => fetch('/api/routes/settings').then(r => r.json())")['settings']  # noqa: E731
            level = lambda k: page.locator(f'[data-tg-level="{k}"]')  # noqa: E731
            flag = lambda k: page.locator(f'[data-tg-flag="{k}"]')  # noqa: E731
            q = lambda key: page.locator(f'[data-norm="{key}"]').input_value()  # noqa: E731

            def open_page():
                page.wait_for_selector('#rsN_tg_levels', state='attached', timeout=30000)
                page.locator('details:has(#rsNorms) > summary').click()
                page.locator('#rsN_tg_levels').scroll_into_view_if_needed()

            page.goto(f'{BASE}/routes/settings')
            open_page()
            levels = {k: level(k).input_value() for k in ('no_contact', 'gps', 'late_plan', 'speed', 'deviation')}
            check(levels == {'no_contact': 'critical', 'gps': 'critical', 'late_plan': 'warning', 'speed': 'warning',
                             'deviation': 'info'}, f'T1 levels by default: {levels}')
            check(not flag('tg_sim_installed').is_checked() and all(flag(k).is_checked() for k in
                                                                     ('tg_report_plan', 'tg_report_summary', 'tg_report_week')),
                  'T1 SIM off, all reports on')
            check((q('tg_escalate_min'), page.locator('#rsN_tg_escalate_to').input_value(), q('tg_summary_at'))
                  == ('10', '838786551', '19:30'), 'T1 escalation 10 min to 838786551, summary by 19:30')
            check(flag('tg_sim_installed').is_visible(), 'T1 checkboxes are visible')
            page.locator('#rsN_tg_levels').screenshot(path=str(SHOTS / 'tg-levels.png'))

            flag('tg_sim_installed').check()
            level('speed').select_option('critical')
            page.locator('[data-norm="tg_escalate_min"]').fill('15')
            page.locator('#rsN_tg_escalate_to').fill('838786551, 12345')
            flag('tg_report_week').uncheck()
            page.locator('[data-norm="tg_summary_at"]').fill('18:45')
            check('is-dirty' in page.locator('#rsDirty').get_attribute('class'), 'T2 form dirty')
            page.locator('#rsSaveBtn').click()
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            s = saved()
            got = (s['tg_sim_installed'], s['tg_levels']['speed'], s['tg_escalate_min'], s['tg_escalate_to'],
                   s['tg_report_week'], s['tg_summary_at'], s['tg_levels']['gps'])
            check(got == (True, 'critical', 15, [838786551, 12345], False, '18:45', 'critical'), f'T2 saved: {got}')

            page.reload()
            open_page()
            check(flag('tg_sim_installed').is_checked() and level('speed').input_value() == 'critical'
                  and page.locator('#rsN_tg_escalate_to').input_value() == '838786551, 12345'
                  and not flag('tg_report_week').is_checked() and q('tg_summary_at') == '18:45'
                  and page.locator('#rsDirty').inner_text() == 'Փոփոխություններ չկան', 'T3 reload shows saved values, form clean')

            page.locator('#rsN_tg_escalate_to').fill('838786551, abc')
            page.locator('#rsSaveBtn').click()
            page.wait_for_timeout(800)
            check(page.locator('#rsN_tg_escalate_to').get_attribute('aria-invalid') == 'true'
                  and saved()['tg_escalate_to'] == [838786551, 12345], 'T4 bad id — error at the field, nothing saved')
            page.locator('#rsN_tg_escalate_to').fill('838786551')
            page.locator('[data-norm="tg_escalate_min"]').fill('241')
            page.locator('#rsSaveBtn').click()
            page.wait_for_timeout(1500)
            check(page.locator('#rsN_tg_escalate_min').get_attribute('aria-invalid') == 'true'
                  and saved()['tg_escalate_min'] == 15, 'T4 escalation 241 — server error at the field, nothing saved')
            page.locator('[data-norm="tg_escalate_min"]').fill('15')

            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(over <= 0, f'P phone settings: no horizontal scroll ({over}px)')
            page.locator('#rsN_tg_levels').scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / 'tg-phone.png'), full_page=False)
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'no page/console errors' + (f': {errors}' if errors else ''))
    print(f'{sum(results)}/{len(results)} OK; screenshots — {SHOTS}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

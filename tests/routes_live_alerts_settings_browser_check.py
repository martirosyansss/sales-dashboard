# -*- coding: utf-8 -*-
"""Настройки тревог карты машин в Telegram (№76, этап 2) в настоящем браузере: «Կարգավորումներ» → «Մեքենաները առցանց՝
ահազանգեր».

Запуск из корня проекта:  python tests/routes_live_alerts_settings_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и заглушка ERP — как в
tests/routes_dispatch_browser_check.py; база маршрутов — временная. Порт 8772 на 127.0.0.1.

S1 по умолчанию: шесть видов тревог отмечены (с «не успеет», №87), тихие часы 20:00–08:00, повтор 30 мин, порог «не успеет»
   без окна 30 мин;
S2 снять «центр», тихие часы 21:00–07:30, повтор 45, порог 45 → форма изменена, «Պահպանել» → в настройках сервера виды без
   «center», 21:00 / 07:30 / 45 / 45;
S3 перезагрузка: поля показывают сохранённое, форма чистая;
S4 повтор 0 — ошибка у поля, настройки не сохранены; порог «не успеет» 4 — тоже;
S5 снять все виды — сохраняется пустой список («ничего не слать»);
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

PORT = 8772
BASE = f'http://127.0.0.1:{PORT}'
SHOTS = Path(tempfile.gettempdir()) / 'live-alerts-settings-check'
KINDS = ['speed', 'stop', 'no_contact', 'gps', 'center', 'late', 'deviation']
NO_CENTER = [k for k in KINDS if k != 'center']


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='live-alerts-settings-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) and '400 (BAD REQUEST)' not in m.text else None)   # 400 — ожидаемый отказ S4
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            saved = lambda: page.evaluate("() => fetch('/api/routes/settings').then(r => r.json())")['settings']  # noqa: E731
            chip = lambda k: page.locator('#rsN_live_alert_kinds label', has=page.locator(f'[data-live-kind][value="{k}"]'))  # noqa: E731
            kinds = lambda: page.eval_on_selector_all('#rsForm [data-live-kind]:checked', 'els => els.map(e => e.value)')  # noqa: E731

            page.goto(f'{BASE}/routes/settings')
            page.wait_for_selector('#rsN_live_alert_kinds', state='attached', timeout=30000)
            page.locator('details:has(#rsNorms) > summary').click()   # нормы и правила свёрнуты — как и пороги тревог
            page.locator('#rsN_live_alert_kinds').scroll_into_view_if_needed()
            check(kinds() == KINDS, f'S1 all kinds checked by default: {kinds()}')
            q = lambda key: page.locator(f'[data-norm="{key}"]').input_value()  # noqa: E731
            check((q('live_quiet_from'), q('live_quiet_to'), q('live_repeat_min'), q('late_nowin_min')) == ('20:00', '08:00', '30', '30'),
                  'S1 quiet 20:00–08:00, repeat 30, late without window 30')
            page.screenshot(path=str(SHOTS / 'settings.png'))

            chip('center').click()   # чип: настоящий чекбокс скрыт, как у рабочих дней
            page.locator('[data-norm="live_quiet_from"]').fill('21:00')
            page.locator('[data-norm="live_quiet_to"]').fill('7:30')    # «7:30» → «07:30»
            page.locator('[data-norm="live_repeat_min"]').fill('45')
            page.locator('[data-norm="live_repeat_min"]').dispatch_event('change')
            page.locator('[data-norm="late_nowin_min"]').fill('45')
            page.locator('[data-norm="late_nowin_min"]').dispatch_event('change')
            check('is-dirty' in page.locator('#rsDirty').get_attribute('class'), 'S2 form dirty')
            page.locator('#rsSaveBtn').click()
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            s = saved()
            check(s['live_alert_kinds'] == NO_CENTER and (s['live_quiet_from'], s['live_quiet_to'], s['live_repeat_min'], s['late_nowin_min'])
                  == ('21:00', '07:30', 45, 45), f'S2 saved: {[s[k] for k in ("live_alert_kinds", "live_quiet_from", "live_quiet_to", "live_repeat_min", "late_nowin_min")]}')

            page.reload()
            page.wait_for_selector('#rsN_live_alert_kinds', state='attached', timeout=30000)
            page.locator('details:has(#rsNorms) > summary').click()
            check(kinds() == NO_CENTER and q('live_quiet_from') == '21:00' and q('live_quiet_to') == '07:30'
                  and q('live_repeat_min') == '45' and q('late_nowin_min') == '45' and page.locator('#rsDirty').inner_text() == 'Փոփոխություններ չկան',
                  'S3 reload shows saved values, form clean')

            page.locator('[data-norm="live_repeat_min"]').fill('0')
            page.locator('#rsSaveBtn').click()
            page.wait_for_timeout(1500)
            err = page.locator('#rsN_live_repeat_min').locator('xpath=ancestor::div[@class="rt-field"]').inner_text()
            check(page.locator('#rsN_live_repeat_min').get_attribute('aria-invalid') == 'true' or 'is-invalid' in
                  (page.locator('#rsN_live_repeat_min').get_attribute('class') or '') or '1' in err,
                  'S4 repeat 0 — error at the field: ' + err.replace('\n', ' ')[:80])
            check(saved()['live_repeat_min'] == 45, 'S4 nothing saved with the error')
            page.locator('[data-norm="live_repeat_min"]').fill('45')
            page.locator('[data-norm="late_nowin_min"]').fill('4')
            page.locator('#rsSaveBtn').click()
            page.wait_for_timeout(1500)
            check(page.locator('#rsN_late_nowin_min').get_attribute('aria-invalid') == 'true' and saved()['late_nowin_min'] == 45,
                  'S4 late threshold 4 — error at the field, nothing saved')
            page.locator('[data-norm="late_nowin_min"]').fill('45')

            for k in KINDS:
                if k in kinds():
                    chip(k).click()
            page.locator('#rsSaveBtn').click()
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            check(saved()['live_alert_kinds'] == [], 'S5 empty list saved («send nothing»)')

            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(over <= 0, f'P phone settings: no horizontal scroll ({over}px)')
            page.locator('#rsN_live_alert_kinds').scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / 'settings-phone.png'))
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'no page/console errors' + (f': {errors}' if errors else ''))
    print(f'{sum(results)}/{len(results)} OK; screenshots — {SHOTS}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

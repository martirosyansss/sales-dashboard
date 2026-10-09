# -*- coding: utf-8 -*-
"""«Գնալ առաջինը» на карте машин (ответ владельца №93) в настоящем браузере: машина, где водитель из-за срока поставил
магазин первым (факт reorders, reason until), — в «Խնդիրներ հիմա» фиолетовая строка-сведение «Վարորդը փոխեց հերթը՝
ժամկետի պատճառով» (важность 3): не мигает, без «ՆՈՐ» и «Տեսա», не в баннере новых проблем и не в заголовке вкладки;
тревоги «порядок» у машины нет.

Запуск из корня проекта:  python tests/routes_live_reorder_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Базы — пустые временные (как
routes_live_browser_check.build_app), факт терминала — подделка; порт на 127.0.0.1 — свободный. Снимок —
_shots/live_reorder.png.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

from routes_live_browser_check import EXTERNAL, PORT, SHOTS, build_app  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from courier import clock  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from test_route_live import A, B, DEPOT, Track, facts, stop  # noqa: E402

TITLE = 'Վարորդը փոխեց հերթը՝ ժամկետի պատճառով'


class FakeLive:
    """CAR1: склад → A (стоит у A); в плане A, B; 10 мин назад «Գնալ առաջինը» у B (срок под риском)."""

    def __init__(self, now):
        tr = Track(now - timedelta(minutes=40)).park(DEPOT, 5).drive(A)
        tr.park(A, int((now - tr.t).total_seconds() // 60))
        device = {'battery': 80, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.4.0',
                  'at': (now - timedelta(minutes=1)).isoformat()}
        f = facts(tr.pts, [stop('S:A', 7, A, 500.0), stop('S:B', 8, B, 200.0, seq=2)],
                  [now - timedelta(minutes=40), now - timedelta(minutes=1)], device, [(now - timedelta(minutes=1), 'on')])
        f['reorders'] = [{'at': (now - timedelta(minutes=10)).isoformat(), 'trip': 1, 'order': ['S:B', 'S:A'],
                          'moved': 'S:B', 'reason': 'until'}]
        self.data = {now.date().isoformat(): {'CAR1': f}}

    def fleet(self, day):
        return self.data.get(day, {})


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    fixed = clock.now().replace(hour=11, minute=0, second=0, microsecond=0)
    clock.now = lambda: fixed
    from route_optimizer import views
    views._yerevan_now = lambda: fixed
    results: list[bool] = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    with tempfile.TemporaryDirectory(prefix='live-reorder-') as tmp:
        app = build_app(Path(tmp))
        state = app.extensions['route_optimizer']
        state.live_facts = FakeLive(fixed)
        draft = dp.Draft(trucks=['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [7, 8])])
        state.store.save_dispatch(fixed.date().isoformat(), draft.to_json(), 'qa')
        server = make_server('127.0.0.1', PORT, app, threaded=True)
        base = f'http://127.0.0.1:{server.server_port}'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        SHOTS.mkdir(exist_ok=True)
        errors: list[str] = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: m.type == 'error' and not (
                    'Failed to load resource' in m.text and any(h in (m.location or {}).get('url', '') for h in EXTERNAL))
                    and errors.append(m.text))
                page.goto(base + '/routes/live')
                page.wait_for_selector('.lv-item')
                row = page.locator('.lv-prob', has_text=TITLE)
                row.first.wait_for(timeout=10000)
                check(row.count() == 1, 'одна строка «Վարորդը փոխեց հերթը՝ ժամկետի պատճառով»')
                cls = row.first.get_attribute('class') or ''
                check('lv-sev3' in cls, f'важность 3 — фиолетовое сведение ({cls})')
                check('is-new' not in cls and 'lv-new' not in cls, 'не новая: не мигает')
                check(row.first.locator('.lv-new-tag').count() == 0, 'без метки «ՆՈՐ»')
                li = page.locator('li[data-key$="|CAR1|reorder"]')
                check(li.count() == 1 and li.locator('.lv-ack').count() == 0, 'без кнопки «Տեսա»')
                check('Խանութ 8' in row.first.inner_text(), 'в строке — перенесённый магазин')
                banner = page.locator('#lvAlarm')
                check(banner.is_hidden() or TITLE not in banner.inner_text(), 'не в баннере новых проблем')
                check('⚠' not in page.title(), f'заголовок вкладки без «⚠» ({page.title()})')
                check(page.locator('.lv-prob', has_text='Խանութներ բաց են թողնված').count() == 0,
                      'тревоги «порядок» нет')
                page.screenshot(path=str(SHOTS / 'live_reorder.png'), full_page=True)
                browser.close()
        finally:
            server.shutdown()
        check(not errors, 'нет ошибок страницы и консоли' + (': ' + '; '.join(errors[:3]) if errors else ''))
    print(f'{sum(results)}/{len(results)} проверок')
    return 0 if all(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

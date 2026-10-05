# -*- coding: utf-8 -*-
"""Фильтр «Մենեջերներ» страницы «Развоз» (/routes/dispatch) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_agents_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение и синтетический день —
как в tests/routes_dispatch_browser_check.py (два менеджера: A001 — магазины 101 и 999 без точки, A002 — 102 и 104);
порт 8767 на 127.0.0.1.

A загрузка: блок «Որ մենեջերների…» виден, два переключателя, оба отмечены, в заголовке «բոլորը՝ 2»;
B «Հանել բոլոր նշումները» → оба сняты, подсказка «Նշեք գոնե մեկ մենեջեր», «Կազմել երթերը» не отправляет запрос;
C отметить A001 → «Տանում ենք՝ 2 պատվեր», подсказка шага 2 «Տանում ենք միայն 1 մենեջերի…»;
D «Կազմել երթերը» → запрос сборки с agents_off [2], в плане нет магазинов 102 и 104, «Կիրառել» скрыта;
E после сборки отметить A002 → «Կիրառել» видна; «Չեղարկել» → снова снят, «Կիրառել» скрыта;
F отметить A002 → «Կիրառել» → правка agents, план без фильтра, 102 и 104 — «ещё не в рейсах»;
S настройки (№69): карточка «Որ մենեջերների…» — два менеджера, снять A002, «Պահպանել» → dispatch_agents_off [2];
R «Начать заново», день без плана: A002 снят правилом, пометка «Կանոնը՝ կարգավորումներից» со ссылкой на карточку,
  выбор дня отличается от правила — пометки нет;
T правило снимает менеджера 99 без заказов дня: «բոլորը՝ 2», без «չենք տանում՝ 0», «Նշել բոլորին» не делает выбор
  изменённым, сборка шлёт [99]; после сборки «Նշել բոլորին» не предлагает «Կիրառել»;
H телефон 390×860: с раскрытым блоком нет горизонтальной прокрутки.
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

PORT = 8767
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-agents-check'


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_app(tempfile.mkdtemp(prefix='dispatch-agents-'), FakeClient())
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, posts = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(viewport={'width': 1440, 'height': 950}).new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            page.on('request', lambda r: posts.append((r.url.rsplit('/', 1)[-1], json.loads(r.post_data or '{}')))
                    if r.method == 'POST' and '/api/routes/dispatch/' in r.url else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            boxes = page.locator('#dpAgentsList input[type="checkbox"]')
            checked = lambda: [boxes.nth(i).is_checked() for i in range(boxes.count())]   # noqa: E731
            data = lambda: page.evaluate("() => fetch('/api/routes/dispatch?date=" + DAY + "').then(r => r.json())")  # noqa: E731

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            check(page.locator('#dpAgents').is_visible(), 'A managers block visible')
            check(boxes.count() == 2 and checked() == [True, True], 'A two managers, both checked')
            check(page.locator('#dpAgentsNote').inner_text() == 'բոլորը՝ 2', 'A summary note: all 2')

            # B
            page.locator('#dpAgents > summary').click()
            page.locator('#dpAgentsNone').click()
            check(checked() == [False, False], 'B «Հանել բոլոր նշումները» unchecks both')
            check('Նշեք գոնե մեկ մենեջեր' in page.locator('#dpAgentsSum').inner_text(), 'B hint: pick at least one')
            page.locator('#dpBuild').click()
            page.wait_for_selector('#dpActionError:not(.d-none)', timeout=5000)
            check(not any(p[0] == 'build' for p in posts), 'B build refused without request')

            # C
            page.locator('#dpAgentsList label', has_text='A001').locator('input').check()
            sum_text = page.locator('#dpAgentsSum').inner_text()
            check('Տանում ենք՝ 2' in sum_text and '«Կազմել երթերը»' in sum_text, 'C kept 2 orders, applies on build: ' + sum_text)
            check('Տանում ենք միայն 1' in page.locator('#dpAttn').inner_text(), 'C step 2 hint about the filter')
            page.screenshot(path=str(SHOTS / 'c-before-build.png'), full_page=True)

            # D
            page.locator('#dpBuild').click()
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            build = [p[1] for p in posts if p[0] == 'build']
            check(len(build) == 1 and build[0].get('agents_off') == [2], 'D build sent agents_off [2]: ' + str(build))
            d = data()
            cids = {s['customer_id'] for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}
            check(d['agents_off'] == [2] and cids == {101}, 'D plan only manager 1 stores: ' + str(cids))
            check(not page.locator('#dpAgentsApplyBox').is_visible(), 'D apply hidden after build')

            # E
            if not page.locator('#dpAgentsList').is_visible():          # после сборки шаг 2 свёрнут
                page.locator('#dpStep2Tog').click()
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            a2 = page.locator('#dpAgentsList label', has_text='A002').locator('input')
            a2.check()
            check(page.locator('#dpAgentsApply').is_visible(), 'E apply visible after change')
            page.screenshot(path=str(SHOTS / 'e-dirty.png'), full_page=True)
            page.locator('#dpAgentsUndo').click()
            check(not a2.is_checked() and not page.locator('#dpAgentsApplyBox').is_visible(), 'E undo restores and hides apply')

            # F
            a2.check()
            n = len(posts)
            page.locator('#dpAgentsApply').click()
            page.wait_for_function("() => document.getElementById('dpAgentsApplyBox').hidden", timeout=15000)
            edits = [p[1] for p in posts[n:] if p[0] == 'edit']
            check(len(edits) == 1 and edits[0].get('action') == 'agents' and edits[0].get('off') == [], 'F edit agents off=[]')
            d = data()
            check(d['agents_off'] == [] and {u['customer_id'] for u in d['plan']['unassigned']} == {102, 104},
                  'F filter cleared, 102/104 unassigned')

            # S — правило в настройках (№69): карточка «Որ մենեջերների…», снять менеджера 2, «Պահպանել»
            page.goto(f'{BASE}/routes/settings#agents')
            page.wait_for_selector('#rsAgentsList', state='visible', timeout=30000)
            rule = page.locator('#rsAgentsList input[type="checkbox"]')
            check(rule.count() == 2 and all(rule.nth(i).is_checked() for i in range(2)), 'S settings card: two managers, both checked')
            page.locator('#rsAgentsList input[value="2"]').uncheck()
            check('1 / 2' in page.locator('#rsStAgents').inner_text() and 'is-dirty' in page.locator('#rsDirty').get_attribute('class'),
                  'S state «1 / 2», form dirty')
            page.screenshot(path=str(SHOTS / 's-settings.png'), full_page=False)
            page.locator('#rsSaveBtn').click()
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            saved = page.evaluate("() => fetch('/api/routes/settings').then(r => r.json())")
            check(saved['settings']['dispatch_agents_off'] == [2], 'S saved dispatch_agents_off [2]')

            # R — день без плана: выбор из правила, пометка «Կանոնը՝ կարգավորումներից» со ссылкой на карточку
            page.evaluate("() => fetch('/api/routes/dispatch/reset', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                          " body: JSON.stringify({date: '" + DAY + "'})})")
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            check(checked() == [True, False], 'R day without plan: manager 2 unchecked by rule')
            note = page.locator('#dpAgentsRule')
            check(note.is_visible() and note.locator('a').get_attribute('href') == '/routes/settings#agents',
                  'R note «Կանոնը՝ կարգավորումներից» links to settings card')
            page.locator('#dpAgentsList label', has_text='A002').locator('input').check()
            check(not note.is_visible(), 'R note hidden once the day choice differs from the rule')

            # T — правило снимает менеджера без заказов этого дня (99): на странице он не виден и ничего не меняет
            page.evaluate("() => fetch('/api/routes/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                          " body: JSON.stringify({settings: {dispatch_agents_off: [99]}})})")
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            check(checked() == [True, True] and page.locator('#dpAgentsNote').inner_text() == 'բոլորը՝ 2',
                  'T hidden rule manager: both listed checked, note «բոլորը՝ 2»')
            check(page.locator('#dpAgentsSum').inner_text() == '' and 'Տանում ենք միայն' not in page.locator('#dpAttn').inner_text(),
                  'T no «չենք տանում՝ 0» summary, no step-2 filter hint')
            page.locator('#dpAgentsAll').click()
            check(note.is_visible(), 'T «Նշել բոլորին» keeps the rule (not dirty)')
            page.locator('#dpAgentsNone').click()
            page.locator('#dpAgentsAll').click()
            n = len(posts)
            page.locator('#dpBuild').click()
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            build = [p[1] for p in posts[n:] if p[0] == 'build']
            check(len(build) == 1 and build[0].get('agents_off') == [99], 'T build keeps hidden rule id: ' + str(build))
            if not page.locator('#dpAgentsList').is_visible():
                page.locator('#dpStep2Tog').click()
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            page.locator('#dpAgentsAll').click()
            check(not page.locator('#dpAgentsApplyBox').is_visible(), 'T planned day: «Նշել բոլորին» does not offer «Կիրառել»')
            page.locator('#dpAgentsNone').click()
            check(page.locator('#dpAgentsApplyBox').is_visible(), 'T planned day: «Հանել բոլոր նշումները» does offer it')
            page.locator('#dpAgentsUndo').click()

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(300)
            if page.locator('#dpAgents').get_attribute('open') is None:
                page.locator('#dpAgents > summary').click()
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            check(over <= 0, f'H phone: no horizontal scroll ({over}px)')
            page.locator('#dpAgents').screenshot(path=str(SHOTS / 'h-phone.png'))
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

# -*- coding: utf-8 -*-
"""Страницы «Маршрутов» в раскладке Tesla+ (фаза 7): «Երթուղիների օպտիմալացում» (Optimize.dc.html), «Կարգավորումներ»
(Settings.dc.html), «Ուսուցում» и «Պահեստ» (табличный образец Garage.dc.html) — в настоящем браузере.

Запуск из корня проекта:  python tests/routes_pages_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение — как в
tests/routes_dispatch_browser_check.py (синтетический день, поддельная ERP, временная папка); для «Օպտիմալացում»
расчёт запускается заранее через API (синтетика — 2 менеджера, 4 магазина). Порт 8783 на 127.0.0.1.

O «Օպտիմալացում»: шапка страницы (H1, «Վերահաշվել», главная «Ներբեռնել պլանը ERP-ի համար» — aria-disabled с подсказкой,
  пока нечего скачивать), три карточки шагов (1 — ✓ и время, 2 — счётчик предложений, текущий — aria-current), параметры
  таблетками и «Փոխել» (раскрывает прежнюю форму), 4 карточки «было → стало» с разницей, «Բոլոր թվերը», менеджеры
  аккордеоном (раскрытый — предложения в его строке), карта дня справа с «Հիմա | Եթե ընդունեք» и днями;
  «Ընդունել» предложения → главная кнопка становится доступной;
S «Կարգավորումներ»: шапка с поиском; оглавление по шагам (группы, состояние каждого блока, прогресс «n / 3»), нажатие —
  к блоку и aria-current; поиск «դիզել» оставляет только подходящие блоки и пункты, Esc — снова всё; жёлтая строка о
  незаполненных обязательных блоках со ссылкой; блоки-панели с таблеткой состояния, обязательный пустой — обводка;
  переключатели role=switch; панель сохранения не видна без изменений, после изменения — «1 չպահպանված փոփոխություն՝
  «Առաքման մեքենաներ»», «Չեղարկել» — снова как на сервере, панель скрыта; L «Ուսուցում», W «Պահեստ» — шапка, KPI-карточки, таблицы;
T обе темы × 390 / 1366 / 1920: тема применена, без горизонтальной прокрутки (tests/tesla_layout_check.py).
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from tesla_layout_check import themes_and_widths  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 8783
BASE = f'http://127.0.0.1:{PORT}'
SHOTS = Path(tempfile.gettempdir()) / 'routes-pages-check'


def build(tmp: str):
    app = base.build_app(tmp, base.FakeClient())

    def admin():
        from flask import g
        g.user_role = 'admin'
    app.before_request_funcs.setdefault(None, []).insert(0, admin)
    app.template_context_processors[None].append(lambda: {'is_admin': True})
    c = app.test_client()
    r = c.post('/api/routes/optimize', json={'agent_ids': None, 'start': 'current', 'frequencies': 'sales'})
    assert r.status_code == 200, r.get_json()
    job = r.get_json()['job_id']
    deadline = time.monotonic() + 60
    while c.get(f'/api/routes/optimize/{job}').get_json()['job']['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(0.1)
    return app


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(parents=True, exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build(tempfile.mkdtemp(prefix='pages-check-'))
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
            page.on('console', lambda m: errors.append(f'console: {m.text}') if m.type == 'error' and not base.is_ignorable(m) else None)
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))

            # O — «Օպտիմալացում»
            page.goto(f'{BASE}/routes/optimize')
            page.wait_for_selector('#roResult:not(.d-none) #roCards .ro-card', timeout=30000)
            page.wait_for_timeout(800)
            check(page.inner_text('#rtOptimize .rt-pagehead__title') == 'Երթուղիների օպտիմալացում'
                  and page.locator('#roHeadRecalc').is_visible() and page.locator('#roHeadExport').is_visible(),
                  'O page head: H1, «Վերահաշվել», «Ներբեռնել պլանը ERP-ի համար»')
            check(page.get_attribute('#roHeadExport', 'aria-disabled') == 'true' and page.inner_text('#roHeadExportHint') != '',
                  f'O nothing accepted yet → the main button is aria-disabled with a hint ({page.inner_text("#roHeadExportHint")!r})')
            check(page.inner_text('#roStepCard1 .ro-stepcard__mark') == '✓' and 'Հաշվված' in page.inner_text('#roStepSub1')
                  and 'առաջարկ' in page.inner_text('#roStepSub2') and page.get_attribute('#roStepCard2', 'aria-current') == 'step',
                  f'O step cards: 1 ✓ «{page.inner_text("#roStepSub1")}», 2 «{page.inner_text("#roStepSub2")}» is current')
            check(page.locator('#roParamPills .ro-param').count() == 4 and page.locator('#roParamsToggle').is_visible()
                  and page.locator('#roForm').is_hidden(), 'O parameters as 4 pills, «Փոխել», the form folded')
            page.click('#roParamsToggle')
            check(page.locator('#roForm').is_visible() and page.locator('#roMore').evaluate('d => d.open'),
                  'O «Փոխել» → the previous parameter form (all fields and modes)')
            page.click('#roParamsToggle')
            kpis = page.locator('#roKpis .rt-kpi')
            check(kpis.count() == 4 and kpis.first.locator('.was').is_visible() and kpis.first.locator('.now').is_visible()
                  and kpis.first.locator('.rt-kpi-delta').is_visible(), f'O «Եթե ընդունեք բոլոր առաջարկները»: {kpis.count()} cards was → now + delta')
            check(page.locator('#roAllBox summary').is_visible(), 'O «Բոլոր թվերը» opens the full table')
            mapbox = page.locator('#roMapBox')
            check(mapbox.evaluate('d => d.open') and page.locator('#roMap .leaflet-container, #roMap.leaflet-container').count() >= 1
                  and page.locator('#roDays .ro-dayb').count() > 0 and page.locator('input[name="roMapMode"]').count() == 2,
                  'O the day map on the right is open: «Հիմա | Եթե ընդունեք», day tiles')
            box_m = page.locator('.ro-mgrs-box').bounding_box()
            box_map = mapbox.bounding_box()
            check(box_m and box_map and box_map['x'] > box_m['x'] + box_m['width'] - 2, f'O managers left, map right ({box_m}, {box_map})')
            btn = page.locator('#roCards .ro-card button[data-open]').first
            agent = btn.get_attribute('data-open')
            btn.click()
            page.wait_for_selector(f'#roCards .ro-card[data-agent="{agent}"] #roPanel:not([hidden])', timeout=5000)
            check(page.locator(f'#roCards .ro-card[data-agent="{agent}"].is-open #roPanel').count() == 1,
                  'O accordion: the open manager row holds its proposals')
            page.screenshot(path=str(SHOTS / 'optimize.png'), full_page=True)
            ok = page.locator('#roPanel .ro-btn-ok').first
            if ok.count():
                ok.click()
                page.wait_for_function("() => document.getElementById('roHeadExport').getAttribute('aria-disabled') === 'false'", timeout=15000)
                check(page.get_attribute('#roHeadExport', 'aria-disabled') == 'false' and page.get_attribute('#roStepCard3', 'aria-current') == 'step',
                      'O «Ընդունել» → the main button available, step 3 current')
                with page.expect_download(timeout=15000) as dl:
                    page.click('#roHeadExport')
                check(dl.value.suggested_filename.endswith('.xlsx'), f'O the head button downloads the plan ({dl.value.suggested_filename})')
                page.click('#roDecBox summary')
                page.once('dialog', lambda d: d.accept())
                page.click('#roDecReset')
                page.wait_for_function("() => document.getElementById('roHeadExport').getAttribute('aria-disabled') === 'true'", timeout=15000)
                check(True, 'O «Չեղարկել բոլորը» → nothing to download again')
            else:
                check(False, 'O an accept button in the open manager')

            # S — «Կարգավորումներ»
            page.goto(f'{BASE}/routes/settings')
            page.wait_for_function("() => !document.getElementById('rsForm').classList.contains('d-none') && document.querySelector('#rsToc .rs-toc-i[data-state]')", timeout=30000)
            page.wait_for_timeout(500)
            groups = page.locator('#rsToc .rs-toc-g')
            items = page.locator('#rsToc .rs-toc-i')
            check(page.inner_text('#rtSettings .rt-pagehead__title') == 'Կարգավորումներ' and page.locator('#rsSearch').is_visible()
                  and groups.count() == 4 and items.count() >= 14, f'S head with search; table of contents {groups.count()} groups, {items.count()} blocks')
            st = page.eval_on_selector_all('#rsToc .rs-toc-i', 'els => els.map(a => [a.dataset.sec, a.dataset.state])')
            check(dict(st).get('depot') == 'ok' and dict(st).get('fuel') in ('todo', 'part') and '/ 3' in page.inner_text('#rsTocG0'),
                  f'S block states in the TOC (depot ✓, fuel !) and progress «{page.inner_text("#rsTocG0")}»')
            check(page.locator('#rsTodo').is_visible() and 'Վառելիքի գներ' in page.inner_text('#rsTodo')
                  and 'is-need' in (page.get_attribute('#fuel', 'class') or ''), 'S yellow reminder about unfilled required blocks, the block outlined')
            page.click('#rsTodoLink')
            page.wait_for_timeout(900)
            check(page.get_attribute('#rsToc .rs-toc-i[data-sec="fuel"]', 'aria-current') == 'true'
                  and page.evaluate("document.getElementById('fuel').getBoundingClientRect().top") < 300, 'S «Լրացնել →» scrolls to the block, TOC marks it current')
            page.click('#rsToc .rs-toc-i[data-sec="season"]')
            page.wait_for_timeout(900)
            check(page.locator('#season').is_visible() and page.locator('#rsAuto').evaluate('d => d.open'), 'S TOC item inside a fold opens it and scrolls')
            page.fill('#rsSearch', 'դիզել')
            page.wait_for_timeout(300)
            vis = page.eval_on_selector_all('#rsToc .rs-toc-i:not([hidden])', 'els => els.map(a => a.dataset.sec)')
            check('fuel' in vis and 'depot' not in vis and page.locator('#fuel').is_visible() and not page.locator('#depot').is_visible(),
                  f'S search «դիզել» → only matching blocks {vis}')
            page.locator('#rsSearch').press('Escape')
            page.wait_for_timeout(300)
            check(page.locator('#depot').is_visible() and page.locator('#rsToc .rs-toc-i:not([hidden])').count() == items.count(), 'S Esc clears the search')
            check(page.locator('#rsTrucks [data-f="active"][role="switch"]').count() > 0, 'S truck «works» toggles are switches (role=switch)')
            check(not page.locator('#rsDock').is_visible() and page.inner_text('#rsDirty') == '' or not page.locator('#rsDock').is_visible(),
                  'S save panel hidden without changes')
            page.locator('#rsTrucks [data-f="active"]').first.click()
            page.wait_for_timeout(300)
            check(page.locator('#rsDock').is_visible() and '1 չպահպանված փոփոխություն' in page.inner_text('#rsDirtyList')
                  and 'Առաքման մեքենաներ' in page.inner_text('#rsDirtyList') and 'is-dirty' in page.get_attribute('#rsDirty', 'class'),
                  f'S change → floating save panel «{page.inner_text("#rsDirtyList")}»')
            page.click('#rsDiscard')
            page.wait_for_function("() => document.getElementById('rsDirty').textContent === 'Փոփոխություններ չկան'", timeout=15000)
            check(not page.locator('#rsDock').is_visible(), 'S «Չեղարկել» → as on the server, panel hidden')
            page.screenshot(path=str(SHOTS / 'settings.png'), full_page=True)
            themes_and_widths(browser, f'{BASE}/routes/settings',
                              lambda p: p.wait_for_function("() => !document.getElementById('rsForm').classList.contains('d-none') && document.querySelector('#rsToc .rs-toc-i[data-state]')", timeout=30000),
                              check, errors, 'T settings')

            themes_and_widths(browser, f'{BASE}/routes/optimize',
                              lambda p: p.wait_for_selector('#roResult:not(.d-none) #roCards .ro-card', timeout=30000),
                              check, errors, 'T optimize')
            browser.close()
    finally:
        server.shutdown()
    for e in errors:
        print('ERR  ' + e)
    check(not errors, f'no page/console errors ({len(errors)})')
    print(f'{sum(results)}/{len(results)} checks OK; screenshots in {SHOTS}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

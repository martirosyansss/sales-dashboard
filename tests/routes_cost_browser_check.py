# -*- coding: utf-8 -*-
"""Проверка страницы «Առաքման արժեք» (/routes/cost, №87 п. 6) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_cost_browser_check.py
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Приложение — то же, что в
tests/routes_dispatch_browser_check.py (настоящие шаблон, статика и blueprint, поддельная ERP, машины CAR1 и CAR2), плюс
два дня планов (01.10 — отправленный водителям, 30.09 — черновик до первой отправки) и подменённые накладные; роль — администратор; всё во
временной папке. Порт 8776 на 127.0.0.1.

A загрузка: вкладка «Առաքման արժեք» текущая; в сноске ставка за км (15 ֏ по умолчанию); плитки заполнены (֏ доставки = Σ таблицы, % от продаж); таблица — 4 магазина,
  по ֏ доставки (aria-sort descending у «Առաքում»); предупреждение «1 օր … պահպանված պլանով»; у магазина без координаты —
  плашка; красных нет (наценка не задана) и плитка подсказывает её задать;
B сортировка: клик «Վաճառքից, %» — по убыванию %, ещё клик — по возрастанию; клик «Խանութ» — по алфавиту;
C поиск: «101» — одна строка; «zzz» — «Որոնմանը համապատասխան խանութ չկա»;
D наценка: 150 — ошибка у поля и aria-invalid, ничего не сохранено; 0,5 — сохранено, есть красные строки с плашкой
  «կարմիր», плитка «Կարմիր խանութներ» = их числу; «Միայն կարմիրները» — только они; пусто — красных снова нет;
E клик по магазину — карточка рейсов (строк = его рейсов, сумма ֏ = его ֏ доставки), «Փակել» — скрыта, фокус на строке;
F период: «90 օր» — запрос days=90 и подзаголовок «90 օր»; «Ընտրել…» — поля дат, 30.09–01.10 → подзаголовок с датами;
G CSV — файл cost-to-serve-….csv с BOM и шапкой, за последний УСПЕШНЫЙ период (неверный период его не подменил);
H телефон 390×860: горизонтальной прокрутки страницы нет (таблица прокручивается внутри карточки).
Ошибки страницы (pageerror) и консоли — провал (кроме сетевых для внешних ресурсов — шрифты, CDN).
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (до Flask: задаёт ROUTES_OSM_PATH и ключ AI)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import cost_to_serve as cts  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402

PORT = 8776   # 8774 заняли проверки «Վարորդներ» и нижней карточки «Развоза»
BASE = f'http://127.0.0.1:{PORT}'
D1, D2 = date(2026, 10, 1), date(2026, 9, 30)   # 30.09 — черновик до первой отправки
NAMES = {101: ('C101', 'Խանութ Ա'), 102: ('C102', 'Խանութ Բ'), 104: ('C104', 'Խանութ Գ'), 999: ('C999', 'Առանց կետի')}
SALES = [cts.Sale(D1, 101, 1, 11, 200_000, 400), cts.Sale(D1, 102, 1, 11, 20_000, 50),
         cts.Sale(D1, 104, 1, 11, 900_000, 1500), cts.Sale(D1, 999, 1, 11, 5_000, 10),
         cts.Sale(D2, 101, 1, 11, 100_000, 300), cts.Sale(D2, 104, 1, 11, 300_000, 800)]


def plan(trips, sent=None):
    raw = dp.Draft(trucks=['CAR1', 'CAR2'], trips=[dp.DraftTrip(i, c, list(s)) for i, (c, s) in enumerate(trips, 1)]).to_json()
    if sent is not None:
        snap = dp.Draft(trucks=['CAR1', 'CAR2'],
                        trips=[dp.DraftTrip(i, c, list(s)) for i, (c, s) in enumerate(sent, 1)]).sent_plan()
        raw['released'] = {'at': '2026-10-01T08:00:00', 'by': 'qa'}
        raw['sent'] = {'at': '2026-10-01T08:00:00', 'by': 'qa', 'plan': snap}
    return raw


def build(tmp):
    app = base.build_app(tmp, base.FakeClient())

    def admin():
        from flask import g
        g.user_role = 'admin'
    app.before_request_funcs.setdefault(None, []).insert(0, admin)   # приложение уже отвечало: before_request() нельзя
    state = app.extensions['route_optimizer']
    state.store.save_dispatch(D1.isoformat(), plan([('CAR1', [104])], sent=[('CAR1', [101, 102]), ('CAR2', [104, 999])]), 'qa')
    state.store.save_dispatch(D2.isoformat(), plan([('CAR2', [101, 104])]), 'qa')
    state.cost_sales_loader = lambda since, until: cts.SalesData(
        tuple(s for s in SALES if since <= s.day < until), {1: 'A001/4'}, NAMES)
    return app


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    tmp = tempfile.mkdtemp(prefix='cost-check-')
    app = build(tmp)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, gets = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1366, 'height': 900}, accept_downloads=True)
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
            # 400 на заведомо неверный период (F) — ожидаемый ответ, браузер всё равно пишет его в консоль
            expected = lambda m: 'from=2026-10-05' in ((m.location or {}).get('url') or '')   # noqa: E731
            page.on('console', lambda m: errors.append(f'console: {m.text}')
                    if m.type == 'error' and not base.is_ignorable(m) and not expected(m) else None)
            page.on('request', lambda r: gets.append(r.url) if '/api/routes/cost?' in r.url else None)
            rows = lambda: page.locator('#ctRows tr')   # noqa: E731
            names = lambda: page.eval_on_selector_all('#ctRows tr td.ct-name button', 'els => els.map(e => e.textContent)')  # noqa: E731

            def num(sel):
                return int(page.inner_text(sel).replace(' ', '').replace(' ', '').replace(',', '') or 0)

            # A
            page.goto(f'{BASE}/routes/cost')
            page.wait_for_selector('#ctRows tr', timeout=30000)
            check(page.get_attribute('#rtSecNav a[aria-current="page"]', 'href') == '/routes/cost', 'A пункт меню раздела текущий')
            check(rows().count() == 4, f'A 4 магазина ({rows().count()})')
            costs = page.eval_on_selector_all('#ctRows td.ct-cost', 'els => els.map(e => +e.textContent.replace(/\\s|\\u00a0/g, ""))')
            check(costs == sorted(costs, reverse=True) and page.get_attribute('th[data-key="cost"]', 'aria-sort') == 'descending',
                  'A по ֏ доставки, дорогие первыми')
            check(num('#ctTotal') == sum(costs) > 0, f'A плитка = Σ таблицы ({num("#ctTotal")} / {sum(costs)})')
            check('պահպանված պլանով' in page.inner_text('#ctWarnText'), 'A предупреждение о дне по черновику')
            check(page.eval_on_selector('#ctRateKm', 'e => e.textContent') == '15', 'A сноска: ставка за км из «Աշխատավարձ» — 15 ֏')
            check(page.locator('#ctRows .rt-badge.b-none').count() == 1, 'A плашка «առանց կոորդինատի» у одного магазина')
            check(page.locator('#ctRows tr.is-red').count() == 0 and 'հավելագինը' in page.inner_text('#ctRedSub'),
                  'A без наценки красных нет, плитка подсказывает')
            check('2 օր, որից պլանով՝ 2' not in page.inner_text('#ctSub') and 'պլանով՝ 2' in page.inner_text('#ctSub'),
                  'A подзаголовок: дней с планом — 2')

            # B
            page.click('th[data-key="pct"] button')
            pcts = page.eval_on_selector_all('#ctRows tr td:last-child', 'els => els.map(e => e.childNodes[0].textContent)')
            vals = [float(x.replace(' ', '').replace(',', '.')) for x in pcts if x[0].isdigit()]
            check(page.get_attribute('th[data-key="pct"]', 'aria-sort') == 'descending' and vals == sorted(vals, reverse=True),
                  'B % — по убыванию')
            page.click('th[data-key="pct"] button')
            pcts = page.eval_on_selector_all('#ctRows tr td:last-child', 'els => els.map(e => e.childNodes[0].textContent)')
            vals = [float(x.replace(' ', '').replace(',', '.')) for x in pcts if x[0].isdigit()]
            check(page.get_attribute('th[data-key="pct"]', 'aria-sort') == 'ascending' and vals == sorted(vals),
                  'B повторный клик — по возрастанию')
            page.click('th[data-key="name"] button')
            got = names()
            check(got == sorted(got), f'B по алфавиту: {got}')

            # C
            page.fill('#ctSearch', '101')
            check(rows().count() == 1 and names() == ['Խանութ Ա'], 'C поиск по коду — одна строка')
            page.fill('#ctSearch', 'zzz')
            check(rows().count() == 0 and page.is_visible('#ctNoMatch'), 'C ничего не найдено — сообщение')
            page.fill('#ctSearch', '')

            # D
            posts = []
            page.on('request', lambda r: posts.append(r.url) if r.method == 'POST' else None)
            page.fill('#ctMargin', '150')
            page.click('#ctMarginSave')
            page.wait_for_function("document.getElementById('ctMarginErr').textContent !== ''")
            check(page.get_attribute('#ctMargin', 'aria-invalid') == 'true' and posts == []
                  and page.inner_text('#ctMarginErr') == 'Թույլատրելի է 0-ից 100', 'D 150 — ошибка у поля, запроса нет')
            check(page.request.get(f'{BASE}/api/routes/cost/margin').json()['value'] is None, 'D ничего не сохранено')
            page.fill('#ctMargin', '0.5')
            page.click('#ctMarginSave')
            page.wait_for_selector('#ctRows tr.is-red', timeout=15000)
            red = page.locator('#ctRows tr.is-red').count()
            check(red > 0 and page.locator('#ctRows .rt-badge.b-danger').count() == red and num('#ctRed') == red,
                  f'D наценка 0,5 % — красных {red}, плитка совпадает')
            check(page.inner_text('#ctPctSub').endswith('հավելագին 0,5%') and 'object' not in page.inner_text('#ctPctSub'),
                  'D плитка % называет наценку')
            check(page.get_attribute('#ctMargin', 'aria-invalid') is None, 'D ошибка поля снята')
            page.check('#ctOnlyRed')
            check(rows().count() == red, 'D «Միայն կարմիրները» — только красные')
            page.uncheck('#ctOnlyRed')
            page.fill('#ctMargin', '')
            page.click('#ctMarginSave')
            page.wait_for_function("document.querySelectorAll('#ctRows tr.is-red').length === 0", timeout=15000)
            check(page.inner_text('#ctRed') == '—', 'D пустая наценка — красных нет')

            # E — самый дорогой магазин (сортировка снова по ֏ доставки)
            page.click('th[data-key="cost"] button')
            first = page.locator('#ctRows tr').first
            store = first.locator('td.ct-name button').inner_text()
            cost = int(first.locator('td.ct-cost').inner_text().replace(' ', '').replace(' ', ''))
            first.locator('td.ct-name button').click()
            page.wait_for_selector('#ctTrips', state='visible')
            totals = page.eval_on_selector_all('#ctTripRows tr td:nth-child(6)',
                                               'els => els.map(e => +e.textContent.replace(/\\s|\\u00a0/g, ""))')
            check(store in page.inner_text('#ctTripsTitle') and totals and sum(totals) == cost,
                  f'E рейсы магазина: {len(totals)} строк, Σ = {sum(totals)} ֏ ({cost})')
            page.click('#ctTripsClose')
            check(page.is_hidden('#ctTrips') and page.evaluate("document.activeElement.closest('#ctRows tr') !== null"),
                  'E «Փակել» — карточка скрыта, фокус на строке')

            # F
            page.click('.ct-period[data-days="90"]')
            page.wait_for_function("document.getElementById('ctSub').textContent.includes('90 օր')", timeout=15000)
            check(any('days=90' in u for u in gets) and page.get_attribute('.ct-period[data-days="90"]', 'aria-pressed') == 'true',
                  'F 90 օր — запрос days=90, кнопка нажата')
            page.click('.ct-period[data-days="custom"]')
            check(page.is_visible('#ctRange'), 'F «Ընտրել…» — поля дат')
            page.fill('#ctFrom', '2026-09-30')
            page.fill('#ctTo', '2026-10-01')
            page.click('#ctRange button[type="submit"]')
            page.wait_for_function("document.getElementById('ctSub').textContent.startsWith('30.09.2026 – 01.10.2026')",
                                   timeout=15000)
            check(any('from=2026-09-30&to=2026-10-01' in u for u in gets), 'F период 30.09–01.10')
            page.fill('#ctFrom', '2026-10-05')                       # начало позже конца — 400, на экране прежний период
            page.click('#ctRange button[type="submit"]')
            page.wait_for_selector('#ctAlert', state='visible')
            check('Սկիզբը վերջից հետո է' in page.inner_text('#ctAlertText'), 'F неверный период — ошибка сервера показана')

            # G
            with page.expect_download() as dl:
                page.click('#ctCsv')
            path = dl.value.path()
            text = Path(path).read_text(encoding='utf-8')
            check(dl.value.suggested_filename == 'cost-to-serve-20260930-20261001.csv' and text.startswith('﻿Առաքման արժեք'),
                  f'G CSV {dl.value.suggested_filename}')

            # H
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(200)
            check(not page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth'),
                  'H горизонтальной прокрутки страницы нет')
            browser.close()
    finally:
        server.shutdown()
    check(not errors, 'ошибок страницы и консоли нет' + (': ' + '; '.join(errors[:3]) if errors else ''))
    print(f'{sum(results)}/{len(results)} проверок')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Проверка переработанной страницы «Развоз» (/routes/dispatch) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium и интернет — Leaflet, шрифты и
Excel-библиотека с CDN). Настоящие ERP и API Anthropic не нужны: Flask-приложение собирается прямо в скрипте
(настоящие шаблон, статика и blueprint route_optimizer, но с поддельной ERP и синтетическим днём из
tests/test_route_optimizer.py, всё во временной папке), клиент модели подменён и в сеть не ходит.
Порт 8766 на 127.0.0.1; плитки карт подменяются серой картинкой.

A загрузка дня 2026-10-01: тело страницы видно, плана нет → шаг 3 скрыт, шаги не свёрнуты, «Փոփոխել 1-ին քայլը» нет,
  кнопка «Կազմել երթերը» доступна;
B «Կազմել երթերը» → на шкале времени появились полосы рейсов, шаги 1 и 2 свёрнуты, кнопка раскрытия шага 1
  видна и раскрывает его;
C клик по полосе рейса → нажата, подпись над картой видна, карточка машины в фокусе и раскрыта;
D в открытой карточке «Փոփոխել» → рейс в режиме правки с выбором «перенести в…»; перенос показывает
  уведомление «տեղափոխվեց»; «Ամրացնել երթը» → значок «ամրացված»; повторный клик по кнопке правки закрывает её;
E ИИ-панель: кнопка открытия → панель с подсказками; подсказка → сообщение пользователя и ответ с пунктом списка,
  у клиента ровно один вызов (в данных дня <day_data); вопрос из поля по Enter → второй ответ с историей из
  двух реплик; Esc закрывает панель, фокус возвращается на кнопку открытия;
F «Տպել» открывает страницу с листами `.sheet` — по одному на машину плана;
G «Վերակազմել/Ջնջել» (сброс, диалог принимается) → шаг 3 снова скрыт;
H телефон 390×860: на построенном плане нет горизонтальной прокрутки, открытая ИИ-панель помещается по ширине.

Ошибки страницы (pageerror) и ошибки консоли — провал, кроме сетевых «Failed to load resource» для внешних
ресурсов (CDN, шрифты, плитки Яндекса/OSM) и /api/routes/road-lines (без карты дорог страница рисует прямые).
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-dummy-for-browser-check'
os.environ['ROUTES_OSM_PATH'] = str(Path(tempfile.gettempdir()) / 'dispatch-check-no-map.osm.pbf')   # карты дорог нет

from flask import Flask  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import route_optimizer  # noqa: E402
from route_optimizer import ai_chat  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, make_snapshot  # noqa: E402

PORT = 8766
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
ANSWER = 'Պատասխան՝ CAR1-ը վերադառնում է ամենաուշը։\n• առաջին\n• երկրորդ'
TILE_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGM4AQAAygDJmcpdfgAAAABJRU5ErkJggg==')
EXTERNAL = ('cdn.jsdelivr.net', 'fonts.googleapis.com', 'fonts.gstatic.com', 'tiles.api-maps.yandex.ru',
            'tile.openstreetmap.org', 'cdnjs.cloudflare.com', 'yandex.ru')


class FakeClient:
    """client.beta.messages.create(**kw) — запоминает запрос, отвечает заготовленным текстом."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=ANSWER)], stop_reason='end_turn',
                               model='claude-sonnet-5-5', _request_id='req_test',
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=100))


def build_app(tmp: str, fake: FakeClient) -> Flask:
    class FakeDb:
        connection_string = 'DRIVER={none};'

    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.secret_key = 'test'
    app.add_url_rule('/logout', 'logout', lambda: 'bye', methods=['GET', 'POST'])   # base_v2.html зовёт url_for('logout')
    app.context_processor(lambda: {'current_user': None, 'current_username': 'qa', 'csrf_token': lambda: 'x'})
    route_optimizer.init_app(app, FakeDb(), db_path=os.path.join(tmp, 'routes.db'))
    app.extensions['route_optimizer'].snapshots = SnapshotCache(lambda: make_snapshot())
    ai_chat._get_client = lambda: fake
    # как в test_api_dispatch_flow: четыре клиента, один без координат; два менеджера
    orders = [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
              _dorder(4, 999, 50.0)]
    with app.test_request_context():
        class _C:       # _dispatch_setup ждёт test-client: он ходит в /api/routes/settings
            application = app
            post = staticmethod(lambda url, json=None: app.test_client().post(url, json=json))
        _dispatch_setup(_C, orders)
    return app


def is_ignorable(msg) -> bool:
    """Сетевые ошибки внешних ресурсов и road-lines — не ошибки страницы (см. docstring)."""
    if 'Failed to load resource' not in msg.text:
        return False
    url = (msg.location or {}).get('url', '') if hasattr(msg, 'location') else ''
    return any(h in url for h in EXTERNAL) or '/api/routes/road-lines' in url


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')    # консоль Windows (cp1252) не печатает армянский
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    fake = FakeClient()
    tmp = tempfile.mkdtemp(prefix='dispatch-check-')
    app = build_app(tmp, fake)
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={'width': 1440, 'height': 950}, accept_downloads=True)
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
            page.on('console', lambda m: errors.append('console: ' + m.text) if m.type == 'error' and not is_ignorable(m) else None)
            page.on('dialog', lambda d: d.accept())
            for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                page.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=TILE_PNG))
            vis = lambda sel: page.locator(sel).is_visible()     # noqa: E731

            # A
            page.goto(f'{BASE}/routes/dispatch?date={DAY}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            check(vis('#dpBody') and not vis('#dpStep3'), 'A body visible, step 3 hidden (no plan)')
            check(page.locator('#dpStep1.is-folded, #dpStep2.is-folded').count() == 0 and not vis('#dpStep1Tog'),
                  'A steps not folded, step 1 toggle hidden')
            check(page.locator('#dpBuild').is_enabled(), 'A build button enabled')

            # B
            page.click('#dpBuild')
            page.wait_for_selector('#dpBoard .dp-bar', timeout=30000)
            page.wait_for_function("() => document.getElementById('dpStep1').classList.contains('is-folded')", timeout=10000)
            bars = page.locator('#dpBoard .dp-bar').count()
            check(bars > 0 and vis('#dpStep3'), f'B plan built, {bars} bars on the timeline')
            check(page.locator('#dpStep1.is-folded').count() == 1 and page.locator('#dpStep2.is-folded').count() == 1
                  and vis('#dpStep1Tog'), 'B steps 1 and 2 folded, step 1 toggle visible')
            page.click('#dpStep1Tog')
            page.wait_for_selector('#dpTrucks', state='visible', timeout=5000)
            check(page.get_attribute('#dpStep1Tog', 'aria-expanded') == 'true' and vis('#dpTrucks'),
                  'B toggle unfolds step 1 (aria-expanded=true, trucks visible)')
            page.click('#dpStep1Tog')       # обратно свёрнут: дальше сценарии как у пользователя
            page.wait_for_function("() => document.getElementById('dpStep1').classList.contains('is-folded')")

            # C
            bar = page.locator('#dpBoard .dp-bar').first
            truck = bar.get_attribute('data-truck')
            bar.click()
            page.wait_for_selector('#dpMapFocus', state='visible', timeout=5000)
            card = page.locator(f'#dpTruckCards .dp-tcard[data-truck="{truck}"]')
            page.wait_for_function("(t) => document.querySelector('#dpTruckCards .dp-tcard[data-truck=\"' + t + '\"]')"
                                   ".classList.contains('is-focus')", arg=truck, timeout=5000)
            check(page.locator('#dpBoard .dp-bar').first.get_attribute('aria-pressed') == 'true', 'C bar aria-pressed=true')
            check(vis('#dpMapFocus') and 'is-focus' in (card.get_attribute('class') or ''), 'C map caption visible, truck card in focus')
            check(card.locator('.dp-thead').get_attribute('aria-expanded') == 'true', 'C focused card header aria-expanded=true')

            # D
            card.locator('.dp-editbtn').first.click()
            page.wait_for_selector('.dp-trip.is-editing select.dp-move', timeout=5000)
            check(page.locator('.dp-trip.is-editing select.dp-move').count() > 0, 'D editing mode: trip has select.dp-move')
            sel = page.locator('.dp-trip.is-editing select.dp-move').first
            opts = sel.locator('option').evaluate_all("els => els.map(o => o.value)")
            target = next((v for v in opts if v.startswith(('t:', 'n:'))), None)
            check(target is not None, f'D move options offered {opts}')
            if target:
                sel.select_option(target)
                page.wait_for_function("() => /տեղափոխվեց/.test((document.getElementById('dpToast') || {}).textContent || '')", timeout=15000)
                check('տեղափոխվեց' in page.inner_text('#dpToast'), f'D toast after move: {page.inner_text("#dpToast")!r}')
            if not page.locator('.dp-trip.is-editing').count():      # перерисовка могла закрыть правку
                page.locator('#dpTruckCards .dp-editbtn').first.click()
                page.wait_for_selector('.dp-trip.is-editing', timeout=5000)
            pin = page.locator('.dp-trip.is-editing .dp-trip-tools button[aria-pressed="false"]').first
            pin.click()
            page.wait_for_selector('.dp-trip .rt-badge.b-ok', timeout=15000)
            check('ամրացված' in page.locator('.dp-trip .rt-badge.b-ok').first.inner_text(), 'D pinned badge «ամրացված» appears')
            if not page.locator('.dp-editbtn[aria-expanded="true"]').count():
                page.locator('#dpTruckCards .dp-editbtn').first.click()
                page.wait_for_selector('.dp-editbtn[aria-expanded="true"]', timeout=5000)
            page.locator('.dp-editbtn[aria-expanded="true"]').first.click()
            page.wait_for_function("() => !document.querySelector('.dp-trip.is-editing')", timeout=5000)
            check(page.locator('.dp-editbtn[aria-expanded="true"]').count() == 0, 'D edit button click closes editing')

            # E
            check(vis('#dpAiOpen'), 'E AI open button visible')
            page.click('#dpAiOpen')
            page.wait_for_selector('#dpAi', state='visible', timeout=5000)
            n_sug = page.locator('#dpAi .dp-ai-sug').count()
            check(vis('#dpAi') and n_sug > 0, f'E AI panel open, {n_sug} suggestions')
            page.locator('#dpAi .dp-ai-sug').first.click()
            page.wait_for_selector('#dpAiLog .dp-ai-msg.is-bot li', timeout=20000)
            check(page.locator('#dpAiLog .dp-ai-msg.is-user').count() == 1
                  and 'առաջին' in page.locator('#dpAiLog .dp-ai-msg.is-bot li').first.inner_text(),
                  'E user message and bot answer with list item')
            first_text = fake.calls[0]['messages'][0]['content'][0]['text'] if fake.calls else ''
            check(len(fake.calls) == 1 and first_text.startswith('<day_data'), f'E exactly one model call, starts with <day_data ({len(fake.calls)} calls)')
            page.fill('#dpAiInput', 'Երկրորդ հարց')
            page.press('#dpAiInput', 'Enter')
            page.wait_for_function("() => document.querySelectorAll('#dpAiLog .dp-ai-msg.is-bot li').length >= 4", timeout=20000)
            hist = fake.calls[1]['messages'] if len(fake.calls) > 1 else []
            check(len(fake.calls) == 2 and page.locator('#dpAiLog .dp-ai-msg.is-bot').count() == 2,
                  f'E second bot message after Enter ({len(fake.calls)} calls)')
            # история — две реплики перед новым вопросом (user + assistant); их видно в messages запроса
            n_turns = len(hist) - 1
            check(n_turns == 2, f'E second request carries history of 2 turns (messages={len(hist)})')
            page.keyboard.press('Escape')
            page.wait_for_function("() => document.getElementById('dpAi').hidden", timeout=5000)
            check(not vis('#dpAi') and page.evaluate("() => document.activeElement && document.activeElement.id") == 'dpAiOpen',
                  'E Escape closes panel, focus back on open button')

            # F
            with ctx.expect_page(timeout=15000) as popup:
                page.click('#dpPrint')
            sheet_page = popup.value
            sheet_page.wait_for_load_state()
            plan_trucks = page.locator('#dpTruckCards .dp-tcard').count()
            n_sheets = sheet_page.locator('.sheet').count()
            check(n_sheets > 0 and n_sheets == plan_trucks, f'F print page: {n_sheets} .sheet for {plan_trucks} trucks in the plan')
            sheet_page.close()

            # H (до сброса — нужен построенный план)
            page.set_viewport_size({'width': 390, 'height': 860})
            page.wait_for_timeout(500)
            sw = page.evaluate('() => document.documentElement.scrollWidth')
            check(sw <= 390, f'H phone 390: plan built, scrollWidth={sw}')
            page.click('#dpAiOpen')
            page.wait_for_selector('#dpAi', state='visible', timeout=5000)
            page.wait_for_timeout(300)
            box = page.locator('#dpAi').bounding_box()
            sw = page.evaluate('() => document.documentElement.scrollWidth')
            check(box and box['x'] >= -1 and box['x'] + box['width'] <= 391 and sw <= 390,
                  f'H phone 390: AI panel fits (x={box and round(box["x"])}, w={box and round(box["width"])}, scrollWidth={sw})')
            page.keyboard.press('Escape')
            page.set_viewport_size({'width': 1440, 'height': 950})
            page.wait_for_timeout(300)

            # G
            page.locator('#dpReset').scroll_into_view_if_needed()
            page.click('#dpReset')
            page.wait_for_function("() => document.getElementById('dpStep3').hidden || "
                                   "getComputedStyle(document.getElementById('dpStep3')).display === 'none'", timeout=20000)
            check(not vis('#dpStep3'), 'G reset → step 3 hidden again')

            check(not errors, 'no pageerror / console errors' + ('' if not errors else ': ' + ' | '.join(errors[:5])))
            browser.close()
    finally:
        server.shutdown()
    print('ALL OK' if all(results) else 'SOME FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

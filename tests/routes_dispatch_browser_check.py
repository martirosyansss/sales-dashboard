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
U «Ժամանակ խանութում» (№50) у точки рейса в режиме правки → диалог с подсказкой по нормам (6 րոպե на тонну, обычные
  8 րոպե), «Հեռացնել» скрыта; пустое поле → диалог закрыт без запроса и уведомления; нечисло — ошибка и aria-invalid,
  ничего не отправлено; 40 → уведомление «40 րոպե» с «Վերակազմեք երթերը», у точки плашка «Բեռնաթափում՝ 40 ր» с
  подсказкой, в базе — только время (окно и допуск магазина целы); снова диалог → «Հեռացնել» → плашки нет, в базе пусто;
  выученная норма 8,37 у магазина без GPS — «սովորական 8,4 րոպե», а не «ըստ փաստի»; 10 стоянок по GPS — время по факту;
  в подсказке — правило №60 (пока стоянок по GPS нет — введённое время, со 2-й — время из GPS), смеси с фактом нет;
E ИИ-панель: кнопка открытия → панель с подсказками; подсказка → сообщение пользователя и ответ с пунктом списка,
  у клиента ровно один вызов (в данных дня <day_data); вопрос из поля по Enter → второй ответ с историей из
  двух реплик; Esc закрывает панель, фокус возвращается на кнопку открытия;
F «Տպել» открывает страницу с листами `.sheet` — по одному на машину плана;
G «Վերակազմել/Ջնջել» (сброс, диалог принимается) → шаг 3 снова скрыт;
H телефон 390×860: на построенном плане нет горизонтальной прокрутки, открытая ИИ-панель помещается по ширине;
V совет «какую машину добавить» (№54; отдельный день 2026-10-02: три заказа по 3,4 т, отмечена только малая машина
  CAR2 без права въезда в центр — магазин центра остался без рейса, CAR1 свободна): карточка называет CAR1 и даёт кнопку «Ավելացնել և վերակազմել»;
  клик отмечает CAR1 в шаге 1 и пересобирает (POST build с CAR1), после чего совет и карточка пропадают;
W устаревший чат (views._seen_stale): план на сервере изменён за спиной страницы (закрепление рейса) → вопрос ИИ
  даёт 409 stale, клиент модели не вызван, в пузыре ошибки «Թարմացնել» и заметка о новом разговоре, вопрос вернулся
  в #dpAiInput; «Թարմացնել» перечитывает день и очищает разговор (снова подсказки); следующий вопрос доходит до
  клиента модели с новым rev.

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
from datetime import date, datetime
from zoneinfo import ZoneInfo
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
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, make_snapshot  # noqa: E402

PORT = 8766
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
CUSTOMERS = {'Клиент <101>': 101, 'Клиент 102': 102, 'Клиент 104': 104}   # магазины дня (_dispatch_setup)
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
            # склад — плашка .rt-pin-depot > span из routes.css (голая иконка светлая на светлой карте не видна)
            pin = page.evaluate("""() => {
                const s = document.querySelector('#dpMap .rt-pin-depot > span');
                if (!s) return null;
                const r = s.getBoundingClientRect(), cs = getComputedStyle(s);
                return { w: r.width, h: r.height, bg: cs.backgroundColor };
            }""")
            check(pin is not None and pin['w'] >= 24 and pin['h'] >= 24 and pin['bg'] not in ('rgba(0, 0, 0, 0)', 'transparent'),
                  f'B depot pin on the map is a filled plate: {pin}')
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

            # U
            store = app.extensions['route_optimizer'].store
            late = st.CustomerWindow('before', 23 * 60 + 59)     # окно всегда выполнено; диалог времени не должен его тронуть
            for c in (101, 102, 104):
                store.save_customer_window(c, late, 'qa')
            posts = []
            page.on('request', lambda r: posts.append(r.url) if r.method == 'POST' and '/customer-vehicles' in r.url else None)
            page.locator('#dpTruckCards .dp-editbtn').first.click()
            page.wait_for_selector('.dp-trip.is-editing .dp-unloadbtn', timeout=5000)
            stop_li = page.locator('.dp-trip.is-editing .dp-stop').filter(has=page.locator('.dp-unloadbtn')).first
            name = stop_li.locator('.dp-stop-main b').inner_text()
            cid = CUSTOMERS[name]

            def open_unload():
                page.locator('.dp-trip.is-editing .dp-stop').filter(has_text=name).locator('.dp-unloadbtn').first.click()
                page.wait_for_selector('#dpUnloadDlg[open]', timeout=5000)
                page.wait_for_function("() => !document.getElementById('dpUnloadMin').disabled", timeout=10000)
                return page.inner_text('#dpUnloadHint').replace('\xa0', ' ')

            hint = open_unload()
            check(page.inner_text('#dpUnloadTitle') == 'Ժամանակ խանութում' and '(6 րոպե տոննայի համար)' in hint
                  and 'Դատարկ՝ սովորական 8 րոպե։' in hint and not vis('#dpUnloadClear'),
                  f'U dialog open, norms in the hint, «Հեռացնել» hidden (no own time): {hint!r}')
            page.click('#dpUnloadSave')                         # пусто у магазина без своего времени — сохранять нечего
            page.wait_for_function("() => !document.getElementById('dpUnloadDlg').open", timeout=5000)
            page.wait_for_timeout(300)
            check(not posts and 'ժամանակը խանութում' not in (page.text_content('#dpToast') or ''),
                  'U empty field without own time → dialog closes, no request, no toast')
            open_unload()
            page.locator('#dpUnloadMin').press_sequentially('e')
            page.click('#dpUnloadSave')
            page.wait_for_function("() => document.getElementById('dpUnloadErr').textContent.trim() !== ''", timeout=5000)
            check(vis('#dpUnloadDlg') and store.load().unload_min == {} and not posts
                  and page.get_attribute('#dpUnloadMin', 'aria-invalid') == 'true',
                  f'U non-numeric input → error, aria-invalid, dialog stays open, nothing sent: {page.inner_text("#dpUnloadErr")!r}')
            page.fill('#dpUnloadMin', '40')
            check(page.get_attribute('#dpUnloadMin', 'aria-invalid') is None, 'U typing clears aria-invalid')
            page.click('#dpUnloadSave')
            page.wait_for_function("() => !document.getElementById('dpUnloadDlg').open", timeout=10000)
            page.wait_for_function("() => /Վերակազմեք երթերը/.test((document.getElementById('dpToast') || {}).textContent || '')",
                                   timeout=10000)
            page.wait_for_selector('.dp-stop .rt-badge.dp-b-unload', timeout=10000)
            badges = page.locator('.dp-stop .rt-badge.dp-b-unload')
            mine = page.locator('.dp-trip .dp-stop').filter(has_text=name).locator('.rt-badge.dp-b-unload')
            check(badges.count() == 1 and mine.count() == 1 and mine.inner_text().replace('\xa0', ' ') == 'Բեռնաթափում՝ 40 ր'
                  and mine.get_attribute('title') == 'խանութի հաստատուն մասը՝ առանց բեռի ժամանակի'
                  and '(40 րոպե)' in page.text_content('#dpToast').replace('\xa0', ' '),
                  f'U saved 40 → toast «40 րոպե» asks to rebuild, badge with title on «{name}»: {badges.count()} badge(s)')
            saved = store.load()
            check(saved.unload_min == {cid: 40.0} and all(saved.windows[c] == late for c in (101, 102, 104)),
                  f'U only the store time is written, windows intact: {saved.unload_min}')
            open_unload()
            check(page.input_value('#dpUnloadMin') == '40' and vis('#dpUnloadClear'), 'U reopened: field 40, «Հեռացնել» visible')
            page.click('#dpUnloadClear')
            page.wait_for_function("() => !document.getElementById('dpUnloadDlg').open", timeout=10000)
            page.wait_for_function("() => !document.querySelector('.dp-stop .rt-badge.dp-b-unload')", timeout=10000)
            saved = store.load()
            check(saved.unload_min == {} and all(saved.windows[c] == late for c in (101, 102, 104)),
                  'U «Հեռացնել» → badge gone, store time cleared, windows intact')
            # подсказка при выученной норме: сервер округляет время по факту до 0,1, а норму — до 0,01 (8,4 и 8,37 —
            # одно «обычное» время); у магазина с разгрузками по GPS — его время по факту и сколько разгрузок
            row = {'per_stop_min': 8.37, 'per_tonne_min': 6.12, 'store_offsets': {}, 'store_stats': {}}
            store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', row)])
            hint = open_unload()
            check('Դատարկ՝ սովորական 8,4 րոպե։' in hint and 'ըստ փաստի' not in hint and '(6,1 րոպե տոննայի համար)' in hint
                  and 'արդեն եղել է' not in hint and '2-րդ բեռնաթափումից սկսած՝ ծրագիրը ժամանակը վերցնում է GPS-ից։' in hint,
                  f'U learned norm 8.37, no GPS visits → «սովորական», not «ըստ փաստի», rule №60 shown: {hint!r}')
            page.click('#dpUnloadCancel')
            # 8,75 → сервер шлёт 8,8, а в JS 8,8 − 8,75 = 0,05000000000000071: сравнение «≥ 0,05» дало бы «ըստ փաստի»
            store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', {**row, 'per_stop_min': 8.75})])
            hint = open_unload()
            check('Դատարկ՝ սովորական 8,8 րոպե։' in hint and 'ըստ փաստի' not in hint,
                  f'U learned norm 8.75 (server 8.8) → «սովորական», not «ըստ փաստի»: {hint!r}')
            page.click('#dpUnloadCancel')
            store.save_learned('2026-09-03', [lr.Outcome('unload', '', True, 'да', {**row, 'store_stats': {str(cid): [10, 20.0]}})])
            hint = open_unload()
            check('(ըստ փաստի)' in hint and 'արդեն եղել է 10 բեռնաթափում։' in hint and 'կհամադրի' not in hint
                  and 'Քանի դեռ այս խանութում GPS-ով 2 բեռնաթափում չկա, օգտագործվում է ձեր գրած ժամանակը' in hint
                  and 'Եթե առաջին երկու բեռնաթափումները տևողությամբ շատ են տարբերվում, ծրագիրը սպասում է երրորդին։' in hint
                  and 'սովորական' not in hint, f'U learned norm + 10 GPS stays → time from GPS (№60): {hint!r}')
            page.click('#dpUnloadCancel')
            store.save_learning_auto('unload', False, 'qa')      # дальше — нормы настроек, как до блока U
            page.locator('.dp-editbtn[aria-expanded="true"]').first.click()
            page.wait_for_function("() => !document.querySelector('.dp-trip.is-editing')", timeout=5000)

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
            # №81: на телефоне рейсы и карта — во вкладках «Երթեր» / «Քարտեզ»; ширину блоков меряем во вкладке рейсов
            check(page.locator('#dpTabs').is_visible(), 'H phone 390: tab bar «Օր · Երթեր · Քարտեզ» after the build')
            page.click('.dp-tab[data-tab="trips"]')
            page.wait_for_timeout(300)
            check(page.locator('#dpTruckCards').is_visible() and not page.locator('#dpTodo').is_visible(),
                  'H phone 390: «Երթեր» shows the trips, not the day')
            # scrollWidth не ловит обрезку внутри карточек (overflow:hidden у предка) — правый край блоков
            # плана, карточек машин и кнопок рейса должен быть внутри #dpStep3
            wide = page.evaluate('''() => {
                const lim = document.getElementById('dpStep3').getBoundingClientRect().right + 1;
                return ['.dp-split', '#dpTruckCards', '.dp-mapcol', '.dp-tcard', '.dp-trip-acts', '.dp-tacts']
                    .flatMap(sel => [...document.querySelectorAll('#dpStep3 ' + sel)]
                        .filter(el => el.offsetParent !== null && el.getBoundingClientRect().right > lim)
                        .map(el => sel + ' ' + Math.round(el.getBoundingClientRect().width) + 'px'));
            }''')
            check(not wide, f'H phone 390: plan blocks fit #dpStep3 (too wide: {wide[:4]})')
            check(not page.locator('#dpAiOpen').is_visible(), 'H phone 390: AI is a tab, the floating button does not cover the page')
            page.click('#dpTabAi')
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

            # V: совет «Չի տեղավորվել». Отдельный день 10-02 (развозим заказы четверга 10-01): три магазина по 3,4 т,
            # CAR2 (3,5 т) берёт два рейса, третий не помещается; CAR1 (10 т) готова, но не отмечена
            heavy = [_dorder(i + 1, cid, 3400.0, day=date(2026, 10, 1)) for i, cid in enumerate((101, 102, 104))]
            names = {101: ('C101', 'Клиент <101>'), 102: ('C102', 'Клиент 102'), 104: ('C104', 'Клиент 104')}
            app.extensions['route_optimizer'].dispatch_loader = lambda since, until, d: dp.DispatchData(
                tuple(heavy), names, {}, {1: ('CAR1',), 2: ('CAR2',)}, datetime(2026, 10, 1, 18, 0))
            DAY2 = '2026-10-02'
            builds, asks = [], []
            page.on('request', lambda r: builds.append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/dispatch/build') else None)
            page.on('request', lambda r: asks.append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/dispatch/ask') else None)
            page.goto(f'{BASE}/routes/dispatch?date={DAY2}')
            page.wait_for_selector('#dpBody', state='visible', timeout=30000)
            page.wait_for_function("() => !document.getElementById('dpBuild').disabled", timeout=15000)
            cb1 = page.locator('#dpTrucks input[type="checkbox"][value="CAR1"]')
            cb2 = page.locator('#dpTrucks input[type="checkbox"][value="CAR2"]')
            check(cb1.is_checked() and cb2.is_checked(), 'V day 10-02: both trucks ticked by default')
            cb1.uncheck()
            page.click('#dpBuild')
            page.wait_for_selector('#dpUnassigned .dp-unassigned', timeout=30000)
            check(len(builds) == 1 and builds[0]['trucks'] == ['CAR2'], f'V first build with CAR2 only: {builds}')
            adv_card = page.locator('#dpUnassigned .dp-unassigned')
            adv_btn = adv_card.locator('button', has_text='Ավելացնել և վերակազմել')
            card_text = adv_card.inner_text()
            check(adv_btn.count() == 1 and 'HOWO · CAR1' in card_text
                  and 'Սեղմեք «Ավելացնել և վերակազմել»' in card_text
                  and page.locator('#dpOverflow button', has_text='Վերակազմել երթերը').count() == 0,
                  f'V advice card (store in the center → center card) names HOWO · CAR1 and has the «Ավելացնել և վերակազմել» button: {card_text!r}')
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY2}').json()
            adv = api_day['plan']['advice']
            check(adv and adv['add'] and adv['add']['car_code'] == 'CAR1' and adv['rebuild'] == [],
                  f'V server plan.advice.add = CAR1: {adv}')
            adv_btn.click()
            page.wait_for_function("() => document.querySelector('#dpUnassigned .dp-unassigned') === null", timeout=30000)
            check(len(builds) == 2 and sorted(builds[1]['trucks']) == ['CAR1', 'CAR2'] and cb1.is_checked(),
                  f'V button ticked CAR1 in step 1 and rebuilt: POST build trucks={builds[-1]["trucks"]}')
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY2}').json()
            check(api_day['plan']['advice'] is None and api_day['plan']['unassigned'] == []
                  and page.locator('#dpOverflow button, #dpUnassigned button', has_text='Ավելացնել').count() == 0,
                  f'V after rebuild: advice gone, nothing unassigned, no advice buttons ({api_day["plan"]["advice"]})')

            # W: на странице план после пересборки (rev1); за её спиной закрепляем рейс — rev2
            page.click('#dpAiOpen')
            page.wait_for_selector('#dpAi', state='visible', timeout=5000)
            page.locator('#dpAi .dp-ai-sug').first.click()
            page.wait_for_selector('#dpAiLog .dp-ai-msg.is-bot li', timeout=20000)
            n_calls = len(fake.calls)
            trip0 = api_day['plan']['trucks'][0]['trips'][0]
            truck0 = api_day['plan']['trucks'][0]['car_code']
            rev1 = api_day['rev']
            pin_resp = page.request.post(f'{BASE}/api/routes/dispatch/edit', data={
                'date': DAY2, 'rev': rev1, 'action': 'pin', 'trip': trip0['id'], 'truck': truck0})
            rev2 = pin_resp.json().get('rev')
            check(pin_resp.status == 200 and isinstance(rev2, int) and rev2 != rev1,
                  f'W plan changed behind the page: rev {rev1} -> {rev2} (status {pin_resp.status})')
            page.fill('#dpAiInput', 'Հին պլանի հարց')
            with page.expect_response(lambda r: r.url.endswith('/dispatch/ask'), timeout=20000) as resp_info:
                page.press('#dpAiInput', 'Enter')
            resp = resp_info.value
            body = resp.json()
            expected_409 = [e for e in errors if '409' in e]          # браузер пишет 409 в консоль — это ожидаемый ответ
            for e in expected_409[:1]:
                errors.remove(e)
            check(resp.status == 409 and body.get('stale') is True and body.get('success') is False
                  and asks and asks[-1].get('rev') == rev1, f'W ask → 409 stale, page sent the old rev: {resp.status} {body}')
            page.wait_for_selector('#dpAiLog .dp-ai-msg.is-err', timeout=5000)
            err = page.locator('#dpAiLog .dp-ai-msg.is-err')
            refresh_btn = err.locator('button', has_text='Թարմացնել')
            check(len(fake.calls) == n_calls, f'W model client got no new call ({len(fake.calls)} vs {n_calls})')
            check(refresh_btn.count() == 1 and 'նոր զրույց' in err.inner_text()
                  and err.locator('button', has_text='Կրկնել').count() == 0,
                  f'W error bubble: «Թարմացնել» button and the new-conversation note: {err.inner_text()!r}')
            check(page.input_value('#dpAiInput') == 'Հին պլանի հարց', 'W question is back in #dpAiInput')
            with page.expect_response(lambda r: r.request.method == 'GET' and '/api/routes/dispatch?' in r.url, timeout=20000):
                refresh_btn.click()
            page.wait_for_function("() => document.querySelectorAll('#dpAiLog .dp-ai-msg').length === 0"
                                   " && document.querySelectorAll('#dpAiLog .dp-ai-sug').length > 0", timeout=10000)
            check(page.locator('#dpAiLog .dp-ai-msg').count() == 0 and page.locator('#dpAiLog .dp-ai-sug').count() > 0
                  and page.input_value('#dpAiInput') == 'Հին պլանի հարց',
                  'W «Թարմացնել» reloads the day and clears the chat (suggestions again, question kept in the field)')
            page.press('#dpAiInput', 'Enter')
            page.wait_for_selector('#dpAiLog .dp-ai-msg.is-bot li', timeout=20000)
            sent = fake.calls[-1]['messages'][0]['content'][0]['text'] if len(fake.calls) > n_calls else ''
            check(len(fake.calls) == n_calls + 1 and asks[-1].get('rev') == rev2 and len(expected_409) == 1
                  and sent.startswith('<day_data') and '"pinned":true' in sent,
                  f'W next question reaches the model: page sent the new rev {rev2} (sent {asks[-1].get("rev")}), '
                  f'day_data shows the pinned trip, calls {len(fake.calls)}')
            page.keyboard.press('Escape')

            # X (№81): план у водителей — только отправленный: черновик → «Հաստատել և ուղարկել» → правка копится → «Ուղարկել»
            # день V (02.10) с его планом — будущий: «сейчас» — 01.10 08:00 (заказы синтетики — только этих дат)
            fixed = datetime(2026, 10, 1, 8, 0)
            real_clock, real_yerevan = views._clock, views._yerevan_now
            views._clock = lambda: fixed
            views._yerevan_now = lambda: fixed.replace(tzinfo=ZoneInfo('Asia/Yerevan'))
            DAY3 = DAY2
            pill = lambda: page.locator('#dpSendState .dp-sendpill')  # noqa: E731
            page.goto(f'{BASE}/routes/dispatch?date={DAY3}')
            page.wait_for_selector('#dpStep3', state='visible', timeout=30000)
            check('is-draft' in (pill().get_attribute('class') or '') and 'հաստատեք' in page.inner_text('#dpTodo')
                  and 'rt-btn-primary' in page.get_attribute('#dpApproveBtn', 'class')
                  and 'rt-btn-ghost' in page.get_attribute('#dpPrint', 'class'),
                  'X after build: «Սևագիր» pill, todo asks to approve, approve is the main button (print secondary)')
            page.click('#dpApproveBtn')
            page.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            check('Ուղարկված է վարորդներին' in pill().inner_text() and page.locator('#dpSendBtn').count() == 0,
                  'X approved: «Ուղարկված է վարորդներին» pill, no send button')
            page.locator('#dpTruckCards .dp-editbtn').first.click()
            sel = page.locator('#dpTruckCards .dp-trip.is-editing .dp-move').first
            opts = sel.locator('option').evaluate_all("os => os.map(o => o.value)")
            target = next((v for v in opts if v.startswith('t:')), None) or next(v for v in opts if v.startswith('n:'))
            sel.select_option(target)
            page.wait_for_selector('#dpSendState .dp-sendpill.is-unsent', timeout=15000)
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY3}').json()
            check(page.locator('#dpSendBtn').is_visible() and 'չեն ուղարկվել' in page.inner_text('#dpTodo')
                  and api_day['unsent'] and api_day['unsent']['trucks'],
                  f'X edit after approval: «չի ուղարկվել» pill + «Ուղարկել», server unsent={api_day["unsent"]}')
            page.click('#dpSendBtn')
            page.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY3}').json()
            check(api_day['unsent'] is None and 'Պլանն ուղարկված է' in page.inner_text('#dpTodo'),
                  f'X «Ուղարկել» → sent, nothing unsent ({api_day["unsent"]})')
            views._clock, views._yerevan_now = real_clock, real_yerevan

            check(not errors, 'no pageerror / console errors' + ('' if not errors else ': ' + ' | '.join(errors[:5])))
            browser.close()
    finally:
        server.shutdown()
    print('ALL OK' if all(results) else 'SOME FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

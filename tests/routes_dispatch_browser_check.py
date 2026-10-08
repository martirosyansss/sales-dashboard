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
import json
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
            page.add_init_script("try { localStorage.setItem('dpLayout', 'list'); } catch (e) {}")   # №82: рабочий экран — блок Y
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
            # №87 п. 4: «Բեռն. №» у каждой точки — обратный объезду (последняя грузится первой)
            ld = sheet_page.locator('.sheet').first.locator('table').first.locator('td.ld').all_inner_texts()
            check('Բեռն. №' in sheet_page.locator('.sheet').first.locator('thead').first.inner_text()
                  and ld == [str(len(ld) - i) for i in range(len(ld))] and len(ld) > 0, f'F loading numbers per stop {ld}')
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
            sent_stops = {t['car_code']: [s['customer_id'] for tr in t['trips'] for s in tr['stops']]
                          for t in page.request.get(f'{BASE}/api/routes/dispatch?date={DAY3}').json()['plan']['trucks']}
            # «Չեղարկել փոփոխությունները» → план снова как у водителей (confirm принимается обработчиком dialog выше)
            page.click('#dpDiscardBtn')
            page.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY3}').json()
            back = {t['car_code']: [s['customer_id'] for tr in t['trips'] for s in tr['stops']] for t in api_day['plan']['trucks']}
            check(api_day['unsent'] is None and back != sent_stops, f'X «Չեղարկել փոփոխությունները» → plan as sent, nothing unsent')
            # снова правка → Excel: сначала окно «Ուղարկել և շարունակել», после него план отправлен
            if page.locator('#dpTruckCards .dp-trip.is-editing').count() == 0:   # «Փոփոխել» мог остаться открытым
                page.locator('#dpTruckCards .dp-editbtn').first.click()
            sel = page.locator('#dpTruckCards .dp-trip.is-editing .dp-move').first
            opts = sel.locator('option').evaluate_all("os => os.map(o => o.value)")
            sel.select_option(next((v for v in opts if v.startswith('t:')), None) or next(v for v in opts if v.startswith('n:')))
            page.wait_for_selector('#dpSendState .dp-sendpill.is-unsent', timeout=15000)
            page.click('#dpExcel')
            page.wait_for_selector('#dpSendFirstDlg[open]', timeout=5000)
            check('չուղարկված' in page.inner_text('#dpSfTitle') and 'Փոխվել են' in page.inner_text('#dpSfLead'),
                  'X Excel with unsent changes → «Ուղարկել և շարունակել» dialog first')
            page.click('#dpSfSend')
            page.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            api_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY3}').json()
            check(api_day['unsent'] is None and 'Պլանն ուղարկված է' in page.inner_text('#dpTodo'),
                  f'X «Ուղարկել և շարունակել» → sent, nothing unsent ({api_day["unsent"]})')
            views._clock, views._yerevan_now = real_clock, real_yerevan

            # Y (№82, вариант А): рабочий экран — карта, машины и шкала с номерами, панель машины, шаги в выдвижной панели
            views._clock = lambda: fixed                    # день V — будущий (в прошедших днях магазины не перетаскиваются)
            views._yerevan_now = lambda: fixed.replace(tzinfo=ZoneInfo('Asia/Yerevan'))
            ws = ctx.new_page()
            ws.add_init_script("try { localStorage.setItem('dpLayout', 'ws'); } catch (e) {}")
            ws_errors = []
            ws.on('pageerror', lambda e: ws_errors.append(str(e)))
            ws.goto(f'{BASE}/routes/dispatch?date={DAY2}')
            ws.wait_for_selector('#rtDispatch.is-ws', timeout=30000)
            ws.wait_for_timeout(800)
            check(ws.locator('#dpWsMap #dpMap').count() == 1 and ws.locator('#dpWsBottom #dpBoard').count() == 1
                  and ws.locator('#dpWsKpi #dpPlanStats').is_visible() and ws.locator('#dpWsActs #dpPrint').is_visible(),
                  'Y workspace: map, board, KPI over the map, print in the header')
            check(ws.locator('#dpBoard .dp-bar i.dp-tick').first.inner_text() == '1'
                  and ws.locator('#dpBoard .dp-blabel .dp-blx-t').first.is_visible(), 'Y board: numbered stops, load line per truck')
            box = ws.evaluate("(() => { const r = document.getElementById('dpWs').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; })()")
            check(box[1] <= box[2] + 1, f'Y workspace fits the window {box}')
            # №86 (до переноса на шкалу ниже): карта ↔ карточка — наведение на кружок шкалы подсвечивает точку; нажатие точки открывает машину и строку;
            # точку можно перетащить на линию другого рейса → правка move (отпустили мимо — ничего)
            ws.locator('#dpBoard .dp-bar i.dp-tick').first.hover()
            ws.wait_for_timeout(300)
            check(ws.locator('.dp-ws-map .leaflet-tooltip').count() >= 1, 'Y hover a board stop → its point on the map is shown')
            ws.mouse.move(5, 5)
            ws.locator('.dp-ws-map .dp-dpin').first.click(force=True)
            ws.wait_for_selector('#dpWsSide:not([hidden]) .dp-stop.is-flash', timeout=5000)
            check(ws.locator('#dpWsSide .dp-tcard.is-focus .dp-stop.is-flash').count() == 1, 'Y click a map point → truck card, the store row flashes')
            ws.click('#dpWsClose')
            ws.wait_for_timeout(600)
            map_moves = []
            ws.on('request', lambda r: map_moves.append(r.post_data) if r.method == 'POST' and r.url.endswith('/dispatch/edit') else None)
            pair = ws.evaluate('''() => { const ps = [...document.querySelectorAll('.dp-ws-map .dp-dpin')].map(e => { const r = e.getBoundingClientRect();
                return {x: r.left + r.width / 2, y: r.top + r.height / 2, c: e.firstElementChild.dataset.trip}; });
              // тянем из рейса, где точек больше одной: оба рейса остаются для переноса на шкалу ниже
              const n = c => ps.filter(p => p.c === c).length;
              for (const a of ps) for (const b of ps) if (a.c !== b.c && n(a.c) > 1 && Math.hypot(a.x - b.x, a.y - b.y) > 40) return [a, b];
              return null; }''')
            dbg = ws.evaluate("[...document.querySelectorAll('.dp-ws-map .dp-dpin')].map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), e.firstElementChild.dataset.trip]; })")
            check(pair is not None, f'Y map has points of two trips to drag between {dbg}')
            if pair:
                a, b = pair
                ws.mouse.move(a['x'], a['y'])
                ws.mouse.down()
                ws.mouse.move((a['x'] + b['x']) / 2, (a['y'] + b['y']) / 2, steps=6)
                ws.mouse.move(b['x'] + 2, b['y'] + 2, steps=6)
                ws.wait_for_timeout(300)
                ws.mouse.up()
                ws.wait_for_timeout(1500)
                check(any('"move"' in (m or '') for m in map_moves), f'Y drag a map point onto another trip line → move ({len(map_moves)} edit)')
            ws.locator('#dpBoard .dp-blabel').first.click()
            ws.wait_for_selector('#dpWsSide:not([hidden]) .dp-tcard.is-focus', timeout=5000)
            check(ws.locator('#dpWsSide .dp-tcard.is-focus .dp-editbtn').first.is_visible(), 'Y truck click → its card on the right with «Փոփոխել»')
            src = ws.locator('#dpWsSide .dp-tcard.is-focus .dp-trip .dp-stop[draggable="true"]').first
            own = ws.evaluate("(document.querySelector('#dpWsSide .dp-tcard.is-focus .dp-trip') || {}).dataset?.trip || ''")
            tgt = ws.locator(f'#dpBoard .dp-bar:not([data-trip="{own}"])').first
            moves = []
            ws.on('request', lambda r: moves.append(r.post_data) if r.method == 'POST' and r.url.endswith('/dispatch/edit') else None)
            check(src.count() > 0 and tgt.count() > 0, f'Y drag source/target present ({src.count()}, {tgt.count()})')
            if src.count() and tgt.count():
                src.drag_to(tgt)
                ws.wait_for_timeout(1500)
                check(any('"move"' in (m or '') for m in moves), f'Y drag a stop from the truck card onto another bar → move ({len(moves)} edit)')
            ws.click('#dpWsClose')
            check(ws.locator('#dpWsSide').is_hidden() and ws.evaluate("document.activeElement && document.activeElement.classList.contains('dp-blabel')"),
                  'Y side closed, focus back on the truck row')
            ws.click('#dpPrepOpen')
            check(ws.locator('#dpDrawer #dpStep1').is_visible() and ws.locator('#dpDrawer #dpBuild').is_visible(),
                  'Y «Մեքենաներ, պատվերներ»: steps 1–2 and rebuild in the drawer')
            ws.click('#dpDrawerClose')
            # шапка рабочего экрана — одной строкой, статус целиком (не «Ու…»), редкие действия — в меню «⋯»
            bar = ws.evaluate("(() => { const b = document.querySelector('#rtDispatch .dp-dayboard'), p = document.querySelector('#dpSendState .dp-sendpill');"
                              " return [b.scrollWidth, b.clientWidth, p ? p.scrollWidth <= p.clientWidth : true]; })()")
            check(bar[0] <= bar[1] and bar[2], f'Y toolbar fits one line, status not clipped {bar}')
            check(ws.locator('#dpViewList').is_hidden() and ws.locator('#dpWsMore').is_visible(), 'Y «Ցուցակով» is inside the «⋯» menu')
            ws.click('#dpWsMore')
            check(ws.get_attribute('#dpWsMore', 'aria-expanded') == 'true' and ws.evaluate("document.activeElement.getAttribute('role')") == 'menuitem',
                  'Y «⋯» opens the menu, focus on the first item')
            ws.keyboard.press('Escape')
            check(ws.locator('#dpWsMenu').is_hidden() and ws.evaluate('document.activeElement.id') == 'dpWsMore', 'Y Esc closes the menu, focus back on «⋯»')
            # правка после отправки → «⋯ → Չեղարկել չուղարկված փոփոխությունները» (подтверждение) → правка discard
            ws.locator('#dpBoard .dp-blabel').first.click()
            ws.wait_for_selector('#dpWsSide:not([hidden]) .dp-tcard.is-focus', timeout=5000)
            if ws.locator('#dpWsSide .dp-trip.is-editing').count() == 0:
                ws.locator('#dpWsSide .dp-tcard.is-focus .dp-editbtn').first.click()
            sel = ws.locator('#dpWsSide .dp-trip.is-editing .dp-move').first
            opts = sel.locator('option').evaluate_all("os => os.map(o => o.value)")
            sel.select_option(next((v for v in opts if v.startswith('t:')), None) or next(v for v in opts if v.startswith('n:')))
            ws.wait_for_selector('#dpSendState .dp-sendpill.is-unsent', timeout=15000)
            ws.click('#dpWsClose')
            ws.click('#dpWsMore')
            asked = []
            ws.once('dialog', lambda d: (asked.append(d.message), d.accept()))
            ws.click('#dpMenuDiscard')
            ws.wait_for_selector('#dpSendState .dp-sendpill.is-sent', timeout=15000)
            check(asked and any('"discard"' in (m or '') for m in moves) and ws.locator('#dpMenuDiscard').is_hidden(),
                  f'Y «⋯» discard: confirmation, edit discard, item gone ({len(asked)} dialog)')
            ws.click('#dpWsMore')
            ws.click('#dpViewList')
            check(ws.locator('#rtDispatch.is-ws').count() == 0 and ws.locator('.dp-split #dpTruckCards').count() == 1
                  and ws.locator('.dp-mapcol #dpMapBox').count() == 1 and ws.locator('#dpViewWs').is_visible(),
                  'Y «Ցուցակով»: the old layout, nodes back home')
            ws.click('#dpViewWs')
            check(ws.locator('#rtDispatch.is-ws').count() == 1 and ws.evaluate("localStorage.getItem('dpLayout')") == 'ws',
                  'Y back to the workspace, remembered')
            ws.set_viewport_size({'width': 1000, 'height': 900})
            ws.wait_for_timeout(300)
            check(ws.locator('#rtDispatch.is-ws').count() == 0, 'Y narrow window: no workspace')
            # Z (№82): ход дня — сегодня кружки магазинов по факту «Առաքիչ» (ответ сервера подменён)
            ws.set_viewport_size({'width': 1440, 'height': 950})
            now2 = datetime(2026, 10, 2, 10, 0)            # «сегодня» — день V с его планом
            views._clock = lambda: now2
            views._yerevan_now = lambda: now2.replace(tzinfo=ZoneInfo('Asia/Yerevan'))
            today_day = page.request.get(f'{BASE}/api/routes/dispatch?date={DAY2}').json()
            check(today_day.get('today') == DAY2 and today_day.get('plan'), 'Z day V is today and has a plan')
            t0 = next((t for t in (today_day.get('plan') or {}).get('trucks', []) if t['trips'] and t['trips'][0]['stops']), None)
            if t0 is not None:
                cids = [s['customer_id'] for s in t0['trips'][0]['stops']]
                # №87: опаздывающий — по окну приёма (на сколько позже конца окна): второй магазин этой машины, а нет его —
                # первый магазин другой машины; у этой машины — «не успевает вернуться»
                other = [(t['car_code'], s['customer_id']) for t in today_day['plan']['trucks'] for tr in t['trips']
                         for s in tr['stops'] if (t['car_code'], s['customer_id']) != (t0['car_code'], cids[0])]
                fake = {'success': True, 'live': True, 'now': '11:00', 'trucks': {t0['car_code']: {
                    str(cids[0]): {'s': 'done', 'at': '09:40', 'delay': None}}},
                    'returns': {t0['car_code']: {'eta': '18:40', 'limit': '18:00', 'late_min': 40}}}
                if other:
                    fake['trucks'].setdefault(other[0][0], {})[str(other[0][1])] = {
                        's': 'late', 'at': '11:30', 'delay': 25, 'late_kind': 'window', 'late_min': 20}
                ws.route('**/api/routes/dispatch/progress**', lambda r: r.fulfill(status=200, content_type='application/json',
                                                                              body=json.dumps(fake)))
                ws.goto(f'{BASE}/routes/dispatch?date={DAY2}')
                ws.wait_for_selector('#dpBoard .dp-tick.pg-done', timeout=15000)
                lab = ws.locator(f'#dpBoard .dp-blabel[data-truck="{t0["car_code"]}"] .dp-blx-pg')
                check(ws.locator('#dpBoard .dp-tick.pg-done').count() >= 1
                      and (not other or ws.locator('#dpBoard .dp-tick.pg-late').count() >= 1)
                      and lab.inner_text().startswith('✓ 1/'), f'Z progress painted: done/late stops, «{lab.inner_text()}» on the truck')
                # №87: подсказка кружка, «↩ +40 ր» у машины, счётчики в строке «требует внимания», нажатие — машина
                if other:
                    tip = ws.locator('#dpBoard .dp-tick.pg-late').first.get_attribute('title') or ''
                    check('Կուշանա պատուհանից 20 րոպեով' in tip and '11:30' in tip, f'Z late tick tooltip: {tip!r}')
                    check(ws.locator('#dpLateChip').inner_text().replace('\xa0', ' ').strip() == '1 խանութ ուշանում է',
                          f'Z late chip: {ws.locator("#dpLateChip").inner_text()!r}')
                bend = ws.locator(f'#dpBoard .dp-bend[data-truck="{t0["car_code"]}"] small')
                check('≈ 18:40' in bend.inner_text() and '+40' in bend.inner_text() and 'pg-back' in (bend.get_attribute('class') or ''),
                      f'Z truck does not make it back: «{bend.inner_text()}» under the return time')
                check(ws.locator('#dpLateBackChip').inner_text().replace('\xa0', ' ').strip() == '1 մեքենա չի հասցնում վերադառնալ',
                      'Z «չի հասցնում վերադառնալ» chip')
                ws.click('#dpLateBackChip')
                ws.wait_for_timeout(600)
                check(ws.locator(f'#dpWsSide:not([hidden]) .dp-tcard.is-focus[data-truck="{t0["car_code"]}"]').count() == 1,
                      'Z chip click focuses the truck (side card)')
                ws.screenshot(path=str(Path(tempfile.gettempdir()) / 'dispatch-late-chips.png'))
            check(not ws_errors, f'Y no page errors {ws_errors[:2]}')
            ws.close()
            views._clock, views._yerevan_now = real_clock, real_yerevan

            check(not errors, 'no pageerror / console errors' + ('' if not errors else ': ' + ' | '.join(errors[:5])))
            browser.close()
    finally:
        server.shutdown()
    print('ALL OK' if all(results) else 'SOME FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

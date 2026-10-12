# -*- coding: utf-8 -*-
"""Раскладка рабочего экрана «Առաքում» (/routes/dispatch) по макету Tesla+ (TeslaPlus.dc.html) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_dispatch_layout_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium). Приложение — как в
tests/routes_dispatch_browser_check.py, но день побольше (build_demo): 6 машин и 48 магазинов вокруг Еревана, часть — в
центре (туда въезжают не все машины); порт 8781 на 127.0.0.1, плитки карт подменяются серой картинкой.

L обе темы ☀/☾ × 1366×768 и 1920×1080: рабочий экран, карта — во всю «сцену» и доходит до низа окна (отступ ≤ 24 px),
  ниже — только список «Խանութներ» (страница прокручивается к нему), вбок прокрутки нет; стеклянные панели: сверху итоги дня и счётчики, слева «Մեքենաներ»
  (строка на машину плана), снизу шкала дня; тема применена;
S выбрали машину в панели слева: строка нажата (aria-pressed), справа — её карточка (номер, экипаж, четыре числа,
  точки, «Բեռնագիր»); на карте выбранный рейс — пронумерованные точки, и каждая видна (не под панелью: elementFromPoint
  в её центре — сама точка); остальные рейсы приглушены, но на карте;
P панели не налезают друг на друга: левая — между верхней и шкалой дня, правая — под верхней до низа, шкала — левее неё;
D второстепенное — в выдвижной панели: «Երթերում չեն» (счётчик) → «Դեռ ոչ մի երթում չեն» в панели; «⋯ → Ինչպես է հաշված» и
  «⋯ → Ղեկավարի համար» — свои части; Esc закрывает, фокус возвращается;
B «Խանութներ» внизу (пользователь 12.10): выбрана машина — её точки (номера — как на карте, акцентом), в карточке строк нет,
  «Խանութները ↓»; имя магазина → страница вверх, точка обведена с подписью; точка на карте → «Ցուցակում» → строка видна и
  подсвечена; «×» и перенос мышью у строки, «Փոփոխել» — кнопки под строками; «Բոլոր մեքենաները» — группы машин (все
  точки дня), группа сворачивается (aria-expanded);
K 1920×1080, обе темы: панели сворачиваются (tests/rt_panels_check.py): «—» → таблетка со счётчиком и фокусом, Enter —
  обратно, «Միայն քարտեզ» и M, M в поле ввода — нет, запоминается после перезагрузки; выбранный рейс снова виден;
T телефон 390×860 (обе темы): вкладки «Քարտեզ · Մեքենաներ · Երթ», на «Քարտեզ» карта видна и высокая, горизонтальной
  прокрутки нет;
Ошибки страницы и консоли — провал (кроме внешних ресурсов, как в основной проверке).
"""
from __future__ import annotations

import dataclasses
import logging
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import routes_dispatch_browser_check as base  # noqa: E402  (переменные окружения и карта дорог — как там)
from rt_panels_check import panels_checks  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import DP_DEPOT, _dorder, make_snapshot  # noqa: E402

PORT = 8781
BASE = f'http://127.0.0.1:{PORT}'
DAY = '2026-10-01'
SHOTS = Path(tempfile.gettempdir()) / 'dispatch-layout-check'

# город → (центр, адрес-улица): точки — по кругу около центра
TOWNS = [('Աբովյան', (40.270, 44.628), 'Հանրապետության'), ('Չարենցավան', (40.400, 44.642), 'Երիտասարդական'),
         ('Արտաշատ', (39.958, 44.548), 'Օգոստոսի 23'), ('Վաղարշապատ', (40.163, 44.292), 'Մաշտոցի'),
         ('Մասիս', (40.066, 44.437), 'Արարատյան'), ('Գառնի', (40.120, 44.730), 'Չարենցի'),
         ('Երևան', (40.181, 44.512), 'Աբովյան'), ('Երևան', (40.205, 44.540), 'Կոմիտասի')]
NAMES = ['Արարատ', 'Նարեկ', 'Գոհար', 'Սոնա', 'Արմինե', 'Լուսինե', 'Տիգրան', 'Հայկ', 'Անի', 'Մարիամ', 'Դավիթ', 'Արեգ']
CARS = [('123AV61', 'HOWO', 10000, True), ('991AT61', 'ISUZU', 5000, True), ('026LS62', 'FORD', 3500, False),
        ('333DO33', 'GAZel', 2500, True), ('504CQ61', 'ISUZU', 5000, False), ('475DD61', 'FORD', 3500, True)]
# водители пяти машин (шестая — свободна), у двух — առաքիչ
CREW = {'123AV61': ('Դ. Գասպարյան', 'Հ. Մանուկյան'), '991AT61': ('Ա. Պետրոսյան', None), '026LS62': ('Ս. Ավետիսյան', None),
        '333DO33': ('Գ. Սարգսյան', 'Վ. Գրիգորյան'), '504CQ61': ('Կ. Հովհաննիսյան', None)}
NOW = datetime(2026, 9, 30, 17, 0)   # «сейчас» — вечер накануне: день 01.10 — завтрашний (не прошедший)


def build_demo(tmp: str):
    """Приложение с днём 01.10 побольше: 6 машин, 48 магазинов в 8 местах (2 — Ереван, часть — малый центр)."""
    app = base.build_app(tmp, base.FakeClient())
    views._clock = lambda: NOW
    views._yerevan_now = lambda: NOW.replace(tzinfo=ZoneInfo('Asia/Yerevan'))
    state = app.extensions['route_optimizer']
    state.driver_list_loader = lambda since, until: [n for pair in CREW.values() for n in pair if n]   # ERP нет
    state.crew_days_loader = lambda since, until: []
    snap = make_snapshot()
    customers, points, default, addresses, names, orders = dict(snap.customers), {}, {}, {}, {}, []
    i = 0
    for ti, (town, (lat, lon), street) in enumerate(TOWNS):
        for k in range(6):
            cid = 3000 + i
            dl, dn = ((k % 3) - 1) * .012 + ti * .001, ((k // 3) * 2 - 1) * .014
            pid = 9000 + i
            points[pid] = (cid, (round(lat + dl, 5), round(lon + dn, 5)))
            default[cid] = pid
            name = f'«{NAMES[(i * 5) % len(NAMES)]}» {"խանութ" if k % 2 else "մարկետ"}'
            customers[cid] = erp.Customer(cid, f'C{cid}', name, '036', 'Այլ', False, '101')
            names[cid] = (f'C{cid}', name)
            addresses[cid] = f'{town}, {street} {3 + k * 7}'
            orders.append(_dorder(100 + i, cid, 80.0 + (i * 37) % 420, agent=1 + i % 2, rev=12000.0 + (i * 1300) % 40000))
            i += 1
    cars = dict(snap.cars)
    cars.update({c: erp.Car(c, m, False) for c, m, _, _ in CARS})
    demo = dataclasses.replace(snap, customers=customers, erp_points={**snap.erp_points, **points},
                               default_address={**snap.default_address, **default}, cars=cars)
    state.snapshots = SnapshotCache(lambda: demo)
    data = dp.DispatchData(tuple(orders), names, addresses, {1: ('123AV61',), 2: ('991AT61',)}, snap.data_as_of)
    state.dispatch_loader = lambda since, until, day: data
    with app.test_request_context():
        r = app.test_client().post('/api/routes/settings', json={
            'settings': {'yerevan_zone': [], 'truck_end_reserve_min': 0, 'morning_loading_from': '01-01', 'morning_loading_to': '12-31'},
            'depot': {'lat': DP_DEPOT[0], 'lon': DP_DEPOT[1]},
            'trucks': [{'car_code': c, 'capacity_kg': kg, 'fuel_l_per_100km': 14 + kg // 1000, 'active': True, 'center_ok': ok}
                       for c, _, kg, ok in CARS] + [{'car_code': c, 'capacity_kg': 3500, 'active': False} for c in ('CAR1', 'CAR2')]})
        assert r.status_code == 200, r.get_json()
        for car, (name, helper) in CREW.items():
            r = app.test_client().post('/api/routes/dispatch/driver', json={'date': DAY, 'car_code': car, 'name': name, **({'helper': helper} if helper else {})})
            assert r.status_code == 200, r.get_json()
        r = app.test_client().post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': [c for c, *_ in CARS]})
        assert r.status_code == 200, r.get_json()
    return app


RECTS = """() => { const r = (s) => { const e = document.querySelector(s); if (!e || !e.offsetParent) return null;
    const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  return { ws: r('#dpWs'), map: r('#dpMap'), top: r('#dpWs .dp-ws-top'), list: r('#dpWsList'), side: r('#dpWsSide'), stops: r('#dpStops'),
           low: r('#dpWs .dp-ws-low'), h: innerHeight, w: innerWidth,
           sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
           sh: document.documentElement.scrollHeight, theme: document.documentElement.dataset.rtTheme }; }"""
# точки выбранного рейса: центр каждой — сама точка (не панель поверх карты), и она внутри карты
HOT_VISIBLE = """() => { const pins = [...document.querySelectorAll('#dpWsMap .dp-npin.is-hot')];
  const bad = pins.filter(p => { const b = p.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
    const e = document.elementFromPoint(x, y); return !e || !p.contains(e); });
  return { n: pins.length, hidden: bad.length, muted: document.querySelectorAll('#dpWsMap .dp-dpin.is-muted').length }; }"""


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    SHOTS.mkdir(parents=True, exist_ok=True)
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    app = build_demo(tempfile.mkdtemp(prefix='dispatch-layout-'))
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            def open_page(theme, w, h):
                pg = browser.new_page(viewport={'width': w, 'height': h})
                pg.add_init_script("try { localStorage.setItem('rtTheme', '%s'); localStorage.setItem('dpLayout', 'ws'); } catch (e) {}" % theme)
                pg.on('pageerror', lambda e: errors.append(f'pageerror {theme} {w}: {e}'))
                pg.on('console', lambda m: errors.append(f'console {theme} {w}: {m.text}')
                      if m.type == 'error' and not base.is_ignorable(m) else None)
                for pat in ('https://tiles.api-maps.yandex.ru/**', 'https://*.tile.openstreetmap.org/**'):
                    pg.route(pat, lambda r: r.fulfill(status=200, content_type='image/png', body=base.TILE_PNG))
                pg.goto(f'{BASE}/routes/dispatch?date={DAY}')
                return pg

            for theme in ('dark', 'light'):
                for w, h in ((1366, 768), (1920, 1080)):
                    tag = f'{theme} {w}×{h}'
                    pg = open_page(theme, w, h)
                    pg.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', timeout=30000)
                    pg.wait_for_timeout(700)
                    r = pg.evaluate(RECTS)
                    check(r['theme'] == theme, f'L {tag}: theme applied ({r["theme"]})')
                    # сцена — до низа окна; ниже (страница прокручивается) — только список «Խանութներ»
                    check(r['ws'] and r['h'] - 24 <= r['ws'][3] <= r['h'] + 1 and r['sw'] <= r['cw'] and r['stops'] and r['stops'][1] >= r['ws'][3],
                          f'L {tag}: stage reaches the window bottom, stores below it, no horizontal scroll (ws={r["ws"]}, h={r["h"]}, stops={r["stops"]}, sw={r["sw"]})')
                    check(r['map'] and r['ws'] and all(abs(a - b) < 2 for a, b in zip(r['map'], r['ws'])),
                          f'L {tag}: the map fills the whole stage {r["map"]} / {r["ws"]}')
                    rows = pg.locator('#dpWsRows .dp-wl-row').count()
                    trucks = len(pg.evaluate("() => fetch('/api/routes/dispatch?date=%s').then(r => r.json()).then(d => d.plan.trucks)" % DAY))
                    check(r['top'] and r['list'] and r['low'] and r['side'] is None and rows == trucks and trucks >= 4,
                          f'L {tag}: glass panels top/left/bottom, a row per plan truck ({rows}/{trucks}), no truck card yet')
                    check(pg.locator('#dpWsKpi #dpPlanStats').is_visible() and pg.locator('#dpWsTop #dpInbox').is_visible(),
                          f'L {tag}: day totals and attention chips in the top panel')
                    # живой отзыв 12.10: строка дня — одной строкой, ничего не обрезано; главная «Հաստատել և ուղարկել» — последней;
                    # подсказка дня — не строкой в панели, а в подсказке статус-таблетки
                    clip = pg.evaluate("""() => [...document.querySelectorAll('.dp-dayboard, #dpSendState .dp-sendpill, #dpWsActs .rt-btn')]
                        .filter(e => e.offsetParent && e.clientWidth > 1 && e.scrollWidth > e.clientWidth + 1).map(e => e.id || e.className)""")
                    row = pg.evaluate("""() => { const kids = ['dpTitle', 'dpDate', 'dpRefresh', 'dpPrepOpen', 'dpWsMore', 'dpApproveBtn']
                        .map(id => document.getElementById(id)).filter(e => e && e.offsetParent).map(e => { const b = e.getBoundingClientRect(); return b.top + b.height / 2; });
                        const acts = [...document.querySelectorAll('#dpWsActs > :not([hidden])')].filter(e => e.offsetParent);
                        return { spread: Math.max(...kids) - Math.min(...kids), last: acts.length ? acts[acts.length - 1].id : '',
                                 h: document.querySelector('.dp-dayboard').getBoundingClientRect().height }; }""")
                    check(not clip and row['spread'] < 12 and row['h'] <= 56 and row['last'] == 'dpApprove',
                          f'L {tag}: day row in one line, nothing clipped, «Հաստատել և ուղարկել» last {row} {clip}')
                    tip = pg.get_attribute('#dpSendState .dp-sendpill', 'title') or ''
                    check(pg.locator('#dpTodo').is_hidden() and 'հաստատեք' in tip, f'L {tag}: day hint in the status pill tooltip ({tip[:60]}…)')
                    # верх видимой карты (низ панели итогов) — не ниже 200 px на 1366×768; панель — одной строкой
                    if (w, h) == (1366, 768):
                        check(r['top'][3] <= 200 and r['top'][3] - r['top'][1] <= 56, f'L {tag}: the map is visible from {r["top"][3]:.0f} px (≤ 200), totals in one row')
                    chips = pg.eval_on_selector_all('#dpInbox .dp-inchip', 'els => els.map(e => [e.querySelector("span").textContent, e.className])')
                    check(chips and chips[-1][0] == 'Լրացնել մեքենաների նորմերը' and 'is-link' in chips[-1][1]
                          and (not any(c[0] == 'Առանց վարորդի' for c in chips) or chips[0][0] == 'Առանց վարորդի'),
                          f'L {tag}: chips by importance, norms — a quiet link at the end {[c[0] for c in chips]}')
                    pg.screenshot(path=str(SHOTS / f'ws-{theme}-{w}.png'))
                    # S — машина из панели слева
                    pg.locator('#dpWsRows .dp-wl-row').nth(1).click()
                    pg.wait_for_selector('#dpWsSide:not([hidden]) .dp-tcard.is-focus', timeout=5000)
                    pg.wait_for_timeout(500)
                    code = pg.locator('#dpWsRows .dp-wl-row').nth(1).get_attribute('data-truck')
                    check(pg.locator('#dpWsRows .dp-wl-row').nth(1).get_attribute('aria-pressed') == 'true'
                          and pg.locator(f'#dpWsSide .dp-tcard.is-focus[data-truck="{code}"]').count() == 1,
                          f'S {tag}: row pressed, its card on the right ({code})')
                    side = pg.locator('#dpWsSide .dp-tcard.is-focus')
                    # карточка компактная: точки рейсов — внизу страницы, в карточке их нет; ссылка «Խանութները ↓»
                    check(side.locator('.dp-tsum-nums > div').count() == 4 and side.locator('.dp-tsum-crew > div').count() == 2
                          and side.locator('.dp-trip-head .dp-editbtn').first.is_visible() and side.locator('.dp-stop').count() == 0
                          and side.locator('.dp-tstops-link').is_visible() and side.locator('.dp-wbbtn').filter(has_text='Բեռնագիր').is_visible(),
                          f'S {tag}: compact card — crew, four numbers, trips with «Փոփոխել», «Խանութները ↓», «Բեռնագիր», no store rows')
                    truck = next(t for t in pg.evaluate("() => fetch('/api/routes/dispatch?date=%s').then(r => r.json()).then(d => d.plan.trucks)" % DAY)
                                 if t['car_code'] == code)
                    n_stops = sum(len(tr['stops']) for tr in truck['trips'])
                    rows = pg.locator('#dpStops .dp-st-r[data-cid]:not(.dp-lunch)')
                    check(code in pg.inner_text('#dpStopsTitle') and rows.count() == n_stops and pg.locator('#dpStops .dp-st-g').count() == 0
                          and pg.locator('#dpStops .dp-st-num.is-hot').count() == n_stops,
                          f'B {tag}: «Խանութներ · {code}» below the map — {rows.count()}/{n_stops} rows, numbers in the accent as on the map')
                    labels_map = sorted(pg.eval_on_selector_all('#dpWsMap .dp-npin.is-hot span', 'els => els.map(e => e.textContent)'))
                    labels_list = sorted(pg.eval_on_selector_all('#dpStops .dp-st-num.is-hot', 'els => els.map(e => e.textContent)'))
                    check(labels_map == labels_list, f'B {tag}: list numbers = map numbers {labels_list[:4]}…')
                    hv = pg.evaluate(HOT_VISIBLE)
                    check(hv['n'] > 0 and hv['hidden'] == 0 and hv['muted'] > 0,
                          f'S {tag}: selected trip points all visible between the panels, other trips muted {hv}')
                    r = pg.evaluate(RECTS)
                    # панель машины — под верхней и до низа сцены, шкала дня — левее неё; панель машин — между верхней и шкалой
                    check(r['side'] and r['top'][3] <= r['side'][1] + 1 and r['low'][2] <= r['side'][0] + 1
                          and r['top'][3] <= r['list'][1] + 1 and r['list'][3] <= r['low'][1] + 1 and r['list'][2] <= r['side'][0],
                          f'P {tag}: panels do not overlap (top {r["top"]}, list {r["list"]}, side {r["side"]}, low {r["low"]})')
                    pg.screenshot(path=str(SHOTS / f'ws-{theme}-{w}-truck.png'))
                    if w == 1366:
                        # B — строка внизу ↔ точка на карте
                        pg.locator('#dpStopsTitle').scroll_into_view_if_needed()
                        row = rows.nth(2)
                        trip = row.evaluate("e => e.closest('.dp-trip').dataset.trip")
                        row.locator('.dp-st-go').click()
                        pg.wait_for_timeout(900)
                        mk = pg.evaluate("() => { const t = document.querySelector('.leaflet-tooltip'); return [scrollY, !!t && !!t.offsetParent]; }")
                        check(mk[0] < 5 and mk[1], f'B {tag}: store name below → page up to the map, the point outlined with its label {mk}')
                        pg.locator(f'#dpWsMap .dp-npin span[data-trip="{trip}"]').nth(1).click(force=True)
                        pg.wait_for_selector('.leaflet-popup .dp-stopcard-list', timeout=5000)
                        pg.click('.leaflet-popup .dp-stopcard-list')
                        pg.wait_for_timeout(900)
                        fl = pg.evaluate("() => { const r = document.querySelector('#dpStops .dp-stop.is-flash'); if (!r) return null;"
                                         " const b = r.getBoundingClientRect(); return [b.top, b.bottom, innerHeight]; }")
                        check(fl and fl[0] >= 0 and fl[1] <= fl[2], f'B {tag}: map point → «Ցուցակում» → its row scrolled into view and flashing {fl}')
                        # действия строки: «×», перенос мышью; «Փոփոխել» — кнопки под строкой
                        check(rows.first.locator('.dp-stop-x').count() == 1 and rows.first.get_attribute('draggable') == 'true',
                              f'B {tag}: row has «×» and can be dragged')
                        pg.evaluate('window.scrollTo(0, 0)')
                        side.locator('.dp-editbtn').first.click()
                        pg.wait_for_timeout(500)
                        check(pg.locator('#dpStops .dp-trip.is-editing .dp-st-r .dp-untilbtn').count() > 0
                              and pg.locator('#dpStops .dp-trip.is-editing .dp-move').count() > 0,
                              f'B {tag}: «Փոփոխել» → store actions (move, «Մինչև ժամը»…) under the rows below')
                        pg.locator('#dpStops').screenshot(path=str(SHOTS / f'stops-{theme}-truck.png'))
                        side.locator('.dp-editbtn').first.click()
                        # все машины — группами, группа сворачивается
                        pg.click('#dpStopsAll')
                        pg.wait_for_timeout(500)
                        groups = pg.locator('#dpStops .dp-st-gbtn')
                        all_rows = pg.locator('#dpStops .dp-st-r[data-cid]:not(.dp-lunch)').count()
                        check(groups.count() == trucks and all_rows == sum(t['stops'] for t in pg.evaluate(
                              "() => fetch('/api/routes/dispatch?date=%s').then(r => r.json()).then(d => d.plan.trucks)" % DAY))
                              and pg.locator('#dpWsSide').is_hidden(),
                              f'B {tag}: no truck selected → all stores grouped by truck ({groups.count()} groups, {all_rows} rows)')
                        groups.first.click()
                        check(pg.locator('#dpStops .dp-st-gbtn').first.get_attribute('aria-expanded') == 'false'
                              and pg.locator('#dpStops .dp-st-r[data-cid]:not(.dp-lunch):visible').count() < all_rows,
                              f'B {tag}: group collapsed (aria-expanded=false), its rows hidden')
                        pg.locator('#dpStops .dp-st-gbtn').first.click()
                        pg.locator('#dpStops').screenshot(path=str(SHOTS / f'stops-{theme}-all.png'))
                        pg.evaluate('window.scrollTo(0, 0)')
                    if w == 1920:
                        # 6b: панели сворачиваются (выбрана машина — есть и её панель)
                        def reload():
                            pg.reload()
                            pg.wait_for_selector('#rtDispatch.is-ws #dpBoard .dp-bar', state='attached', timeout=30000)
                            # панель машин может быть свёрнута — выбрать машину как её строкой (нажатие из кода)
                            pg.evaluate("document.querySelectorAll('#dpWsRows .dp-wl-row')[1].click()")
                            pg.wait_for_timeout(700)
                        panels_checks(pg, check, [('top', '#dpWsTop', 'խանութ'), ('list', '#dpWsList', str(trucks)),
                                                  ('side', '#dpWsSide', code), ('board', '#dpWsLow', None)],
                                      f'K {tag}', reload, '#dpDate')
                        hv = pg.evaluate(HOT_VISIBLE)
                        check(hv['n'] > 0 and hv['hidden'] == 0, f'K {tag}: after collapsing/expanding the selected trip is still visible {hv}')
                    if w == 1366:
                        # живой отзыв 12.10: «Տպել» и «Excel» — в меню «⋯» (клавиатура: стрелки), работают как раньше
                        pg.click('#dpWsMore')
                        items = pg.eval_on_selector_all('#dpWsMenu [role="menuitem"]:not([hidden])', 'els => els.map(e => e.id)')
                        check(items[:2] == ['dpMenuPrint', 'dpMenuExcel'] and pg.locator('#dpPrint').is_hidden() and pg.locator('#dpExcel').is_hidden(),
                              f'P {tag}: «Տպել» and «Excel» are the first items of «⋯» {items}')
                        with pg.context.expect_page(timeout=15000) as popup:
                            pg.keyboard.press('Enter')    # фокус — на первом пункте («Տպել»)
                        sheet = popup.value
                        sheet.wait_for_load_state()
                        check(sheet.locator('.sheet').count() == trucks, f'P {tag}: «⋯ → Տպել» opens the driver sheets ({sheet.locator(".sheet").count()})')
                        sheet.close()
                        pg.click('#dpWsMore')
                        pg.keyboard.press('ArrowDown')
                        check(pg.evaluate('document.activeElement.id') == 'dpMenuExcel', 'P «⋯»: ArrowDown → «Excel»')
                        with pg.expect_download(timeout=30000) as dl:
                            pg.keyboard.press('Enter')
                        check(dl.value.suggested_filename.endswith('.xlsx'), f'P {tag}: «⋯ → Excel» downloads {dl.value.suggested_filename}')
                    if theme == 'dark' and w == 1366:
                        # D — второстепенное в выдвижной панели
                        chip = pg.locator('#dpInbox .dp-inchip', has_text='Երթերում չեն')
                        if chip.count():
                            chip.click()
                            check(pg.locator('#dpSheet').is_visible() and pg.locator('#dpSheet #dpUnassigned .dp-unassigned').is_visible(),
                                  'D «Երթերում չեն» → «Դեռ ոչ մի երթում չեն» in the side sheet')
                            pg.keyboard.press('Escape')
                            check(pg.locator('#dpSheet').is_hidden(), 'D Esc closes the sheet')
                        for item, inside in (('#dpMenuCalc', '#dpSheet #dpMainLead'), ('#dpMenuBoss', '#dpSheet #dpAnalysis')):
                            pg.click('#dpWsMore')
                            pg.click(item)
                            check(pg.locator('#dpSheet').is_visible() and pg.locator(inside).is_visible(),
                                  f'D «⋯ → {pg.locator(item).inner_text()}» opens its part')
                            pg.screenshot(path=str(SHOTS / f'sheet-{item[1:]}.png'))
                            pg.click('#dpSheetClose')
                            check(pg.locator('#dpSheet').is_hidden() and pg.evaluate('document.activeElement.id') == 'dpWsMore',
                                  'D «×» closes, focus back on «⋯»')
                    pg.close()
                # T — телефон
                pg = open_page(theme, 390, 860)
                pg.wait_for_selector('#dpTabs:not([hidden])', timeout=30000)
                labels = [x.strip() for x in pg.locator('#dpTabs .dp-tab:not([hidden])').all_inner_texts()]
                check(labels[:3] == ['Քարտեզ', 'Մեքենաներ', 'Երթ'], f'T {theme}: tabs {labels}')
                pg.click('.dp-tab[data-tab="map"]')
                pg.wait_for_timeout(500)
                m = pg.evaluate("(() => { const b = document.getElementById('dpMap').getBoundingClientRect(); return [b.width, b.height, b.top]; })()")
                sw = pg.evaluate('document.documentElement.scrollWidth')
                check(m[0] >= 340 and m[1] >= 380 and sw <= 390, f'T {theme}: «Քարտեզ» — the map is visible and tall {m}, scrollWidth={sw}')
                pg.screenshot(path=str(SHOTS / f'phone-{theme}-map.png'))
                for tab in ('day', 'trips'):
                    pg.click(f'.dp-tab[data-tab="{tab}"]')
                    pg.wait_for_timeout(300)
                    sw = pg.evaluate('document.documentElement.scrollWidth')
                    check(sw <= 390, f'T {theme}: tab {tab} — no horizontal scroll ({sw})')
                    pg.screenshot(path=str(SHOTS / f'phone-{theme}-{tab}.png'), full_page=True)
                pg.close()
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

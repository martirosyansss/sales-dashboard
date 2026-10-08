# -*- coding: utf-8 -*-
"""Проверка «Մեքենաները առցանց» (№76) в настоящем браузере на КОПИИ баз и синтетическом дне.

Запуск из корня проекта:  python tests/routes_live_browser_check.py [папка_с_базами]
Имя без префикса test_: pytest его не собирает (нужен Playwright с Chromium). Базы route_optimizer.db и courier.db из
папки (по умолчанию — корень проекта) копируются во временную папку через sqlite backup (только чтение источника);
дальше всё пишется только в копию: синтетический план «Развоза» на сегодня и день четырёх машин парка копии (трек,
доставки, состояние терминала): в пути с превышением скорости, долгая стоянка не у магазина, нет связи и GPS выключен,
на складе до выезда. Порт 8771 на 127.0.0.1. Снимки — в _shots/ (ПК 1440×900 и телефон 390×860).

Этап 2 (06.10): у машины 1 — возврат товара и две заправки (интервал «полный бак → полный бак» против расчёта),
у неё же карточка показывает «բեռնված այս երթում», возврат на борту, сверку топлива, источник ETA («ճանապարհներով» /
«մոտավոր» — карты дорог в проверке нет, значит «մոտավոր») и ETA оставшихся магазинов.

№87 «не успеет»: окно приёма третьего магазина машины 1 кончилось полчаса назад — в списке «1 խանութ ուշանում է», в
карточке — активная строка «Կուշանա պատուհանից N րոպեով», магазин в списке точек отмечен.

Как телематика (08.10): блок «Խնդիրներ հիմա» (строка — кнопка выбора машины), у машины 3 без связи — «կապ չկա N րոպե»
вместо опоздания и полупрозрачный маркер с «վերջինը՝ HH:MM», таблица «план — факт» магазинов, воспроизведение дня
(ползунок времени, «Փակել»).

Проверяется: список и маркеры всех машин, состояние и счётчик тревог, карточка выбранной машины (поля №76), путь и
магазины на карте, нет горизонтальной прокрутки на телефоне, опрос раз в 15 с, нет ошибок страницы и консоли (кроме сетевых
ошибок внешних ресурсов: шрифты, CDN, плитки).
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import sys
import tempfile
import threading
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['ROUTES_OSM_PATH'] = str(Path(tempfile.gettempdir()) / 'live-check-no-map.osm.pbf')   # карты дорог нет
os.environ.pop('COURIER_DEMO', None)

from flask import Flask  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import courier  # noqa: E402
import route_optimizer  # noqa: E402
from courier import clock, events as ev  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import live  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402

PORT = 8771
BASE = f'http://127.0.0.1:{PORT}'
SHOTS = ROOT / '_shots'
EXTERNAL = ('cdn.jsdelivr.net', 'fonts.googleapis.com', 'fonts.gstatic.com', 'tile.openstreetmap.org', 'cdnjs.cloudflare.com')


def copy_db(src: Path, dst: Path) -> None:
    """Копия базы через sqlite backup; источник открыт только для чтения (живая база сервера не меняется)."""
    if not src.exists():
        return
    with sqlite3.connect(f'file:{src.as_posix()}?mode=ro', uri=True) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def build_app(tmp: Path) -> Flask:
    class FakeDb:
        connection_string = 'DRIVER={none};'

    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.secret_key = 'test'
    app.add_url_rule('/logout', 'logout', lambda: 'bye', methods=['GET', 'POST'])
    app.context_processor(lambda: {'current_user': None, 'current_username': 'qa', 'csrf_token': lambda: 'x',
                                   'is_admin': True, 'is_garage': False})
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp / 'route_optimizer.db'))
    courier.init_app(app, FakeDb(), db_path=str(tmp / 'courier.db'))
    route_optimizer.attach_live_facts(app, courier.live_facts(app))
    return app


def seed(app: Flask) -> list[str]:
    """Синтетический день четырёх машин копии (план «Развоза» и факт терминалов). Возвращает номера машин."""
    rs = app.extensions['route_optimizer'].store
    cs = app.extensions['courier'].store
    bundle = rs.load()
    depot = bundle.depot or (40.1360, 44.4710)
    cars = [c for c, t in sorted(bundle.trucks.items()) if t.capacity_kg and t.fuel_l_per_100km][:4]
    while len(cars) < 4:
        cars.append(f'TEST{len(cars) + 1}')
    now = clock.now()
    day = now.date().isoformat()
    shops = [(40.1772, 44.5126), (40.1912, 44.5150), (40.2050, 44.4930), (40.1640, 44.4870), (40.1850, 44.4650),
             (40.2110, 44.5350), (40.1530, 44.5200), (40.1950, 44.5520)]
    trips, pred = [], {}
    stops_of: dict[str, list[dict]] = {}
    for n, car in enumerate(cars):
        mine = [shops[(n * 2 + k) % len(shops)] for k in range(3)]
        cids = [900000 + n * 10 + k for k in range(3)]
        trips.append(dp.DraftTrip(n + 1, car, cids))
        start = now - timedelta(hours=2)
        pred[car] = {'trips': [{'depart': (start + timedelta(minutes=10)).strftime('%H:%M'),
                                'stops': [[c, (start + timedelta(minutes=35 + 30 * k)).strftime('%H:%M')]
                                          for k, c in enumerate(cids)]}]}
        stops_of[car] = [{'stop_id': f'S:{str(uuid.uuid4()).upper()}', 'seq': k + 1, 'collect': 'none', 'doc_number': f'{n}{k}',
                          'lat': p[0], 'lon': p[1], 'customer': {'id': cid, 'name': f'Խանութ «Արարատ {n}{k}»'},
                          'amount_due': 0, 'weight_kg': 300.0 + 100 * k,
                          'lines': [{'line_id': 'a', 'qty': 10, 'price': 100, 'product_id': 1, 'marked': False,
                                     'weight_kg': 300.0 + 100 * k},
                                    {'line_id': 'b', 'qty': 2, 'price': 100, 'product_id': 2, 'marked': False,
                                     'weight_kg': None}]}
                         for k, (p, cid) in enumerate(zip(mine, cids))]
        cs.save_day(day, car, stops_of[car], 'live-check', now.isoformat())
    draft = dp.Draft(trucks=list(cars), trips=trips)
    draft.prediction = {'trucks': pred}
    rs.save_dispatch(day, draft.to_json(), 'live-check')
    # №87: окно приёма третьего магазина машины 1 кончилось 30 минут назад — машина к нему опаздывает
    rs.save_customer_window(trips[0].stops[2], st.CustomerWindow('before', max(0, now.hour * 60 + now.minute - 30)),
                            'live-check')

    def path(points, start, step_s=15, speed=11.0):
        out, t = [], start
        for a, b in zip(points, points[1:]):
            steps = max(1, math.ceil(haversine_km(a, b) * 1000 / (speed * step_s)))
            for i in range(1, steps + 1):
                f = i / steps
                t += timedelta(seconds=step_s)
                out.append((t, a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, speed, 45.0))
        return out, t

    def park(p, start, minutes):
        return [(start + timedelta(minutes=i), p[0], p[1], 0.0, None) for i in range(int(minutes) + 1)], \
            start + timedelta(minutes=int(minutes))

    whos: dict[str, ev.Who] = {}

    def send(car, pts, extra=(), device=None, gps='on'):
        if car not in whos:
            did = cs.save_driver(None, f'Վարորդ {car}', True, str(4000 + cars.index(car)), 'admin')
            term, _ = cs.create_terminal('Live ' + car, car, 'admin')
            whos[car] = ev.Who(term.id, car, did, f'Վարորդ {car}')
        who = whos[car]
        batch = []
        for i in range(0, len(pts), 60):
            chunk = pts[i:i + 60]
            payload = {'points': [{'at': t.isoformat(), 'lat': la, 'lon': lo, 'acc': 9.0, 'spd': s, 'brg': b}
                                  for t, la, lo, s, b in chunk]}
            if device:
                payload['device'] = {**device, 'gps': gps}
            batch.append({'id': str(uuid.uuid4()), 'type': 'track', 'stop_id': None, 'date': day,
                          'at': chunk[-1][0].isoformat(), 'payload': payload})
        batch += list(extra)
        r = ev.ingest(cs, who, batch).json()
        assert not r['rejected'], r['rejected']

    def delivery(sid, at, share=1.0):
        return {'id': str(uuid.uuid4()), 'type': 'delivery', 'stop_id': sid, 'date': day, 'at': at.isoformat(),
                'payload': {'lines': [{'line_id': 'a', 'qty': 10 * share}, {'line_id': 'b', 'qty': 2 * share}]}}

    dev = {'battery': 76, 'charging': False, 'net': 'cell', 'app': '2.2.0'}
    t0 = now - timedelta(hours=2)
    # 1) в пути: два магазина сделаны (второй — частично), едет к третьему; было превышение скорости
    s = stops_of[cars[0]]
    a, t = park(depot, t0, 10)
    b, t = path([depot, (s[0]['lat'], s[0]['lon'])], t)
    c, t = park((s[0]['lat'], s[0]['lon']), t, 9)
    t_a = t
    d, t = path([(s[0]['lat'], s[0]['lon']), (s[1]['lat'], s[1]['lon'])], t, speed=27.0)
    e, t = park((s[1]['lat'], s[1]['lon']), t, 8)
    t_b = t
    mid = ((s[1]['lat'] + s[2]['lat']) / 2, (s[1]['lon'] + s[2]['lon']) / 2)
    f, t = path([(s[1]['lat'], s[1]['lon']), mid], now - timedelta(minutes=4))
    f = [x for x in f if x[0] <= now]
    extra = [delivery(s[0]['stop_id'], t_a - timedelta(minutes=2)), delivery(s[1]['stop_id'], t_b - timedelta(minutes=2), 0.5),
             # этап 2: возврат 3 пачек товара 1 в магазине 2 и две заправки «до полного бака» (400 км по одометру, 80 л)
             {'id': str(uuid.uuid4()), 'type': 'return', 'stop_id': s[1]['stop_id'], 'date': day,
              'at': (t_b - timedelta(minutes=1)).isoformat(), 'payload': {'product_id': 1, 'qty': 3}},
             {'id': str(uuid.uuid4()), 'type': 'refuel', 'stop_id': None, 'date': day,
              'at': (now - timedelta(minutes=110)).isoformat(),
              'payload': {'liters': 80.0, 'odometer_km': 10400, 'full_tank': True}}]
    send(cars[0], a + b + c + d + e + f, extra, dev)
    # 2) долгая стоянка не у магазина (идёт сейчас), батарея низкая
    s = stops_of[cars[1]]
    away = (40.1725, 44.5390)
    a, t = park(depot, t0, 10)
    b, t = path([depot, (s[0]['lat'], s[0]['lon'])], t)
    c, t = park((s[0]['lat'], s[0]['lon']), t, 6)
    t_a = t
    d, t = path([(s[0]['lat'], s[0]['lon']), away], t)
    e, t = park(away, t, max(1, int((now - t).total_seconds() // 60)))
    send(cars[1], a + b + c + d + e, [delivery(s[0]['stop_id'], t_a - timedelta(minutes=1))],
         {**dev, 'battery': 14, 'charging': False})
    # 3) нет связи 12 мин, перед этим GPS выключен
    s = stops_of[cars[2]]
    a, t = park(depot, t0, 10)
    b, t = path([depot, (s[0]['lat'], s[0]['lon'])], t)
    lost = now - timedelta(minutes=12)
    real_now = clock.now
    clock.now = lambda: lost - timedelta(minutes=2)          # получено сервером тогда, а не сейчас
    send(cars[2], [p for p in a + b if p[0] <= lost - timedelta(minutes=2)], (), dev)
    clock.now = lambda: lost
    send(cars[2], [], ({'id': str(uuid.uuid4()), 'type': 'track', 'stop_id': None, 'date': day, 'at': lost.isoformat(),
                        'payload': {'points': [], 'device': {**dev, 'gps': 'off'}}},), None)
    clock.now = real_now
    # 4) на складе, загрузка
    a, t = park(depot, now - timedelta(minutes=25), 24)
    send(cars[3], a, (), {**dev, 'charging': True, 'net': 'wifi'})
    return cars


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    results: list[bool] = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    with tempfile.TemporaryDirectory(prefix='live-check-') as tmp:
        tmp_p = Path(tmp)
        for name in ('route_optimizer.db', 'courier.db'):
            copy_db(src / name, tmp_p / name)
        app = build_app(tmp_p)
        with app.app_context():
            cars = seed(app)
        server = make_server('127.0.0.1', PORT, app, threaded=True)
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
                polls: list[str] = []
                page.on('request', lambda r: polls.append(r.url) if ('/api/routes/live?' in r.url
                                                                     or r.url.endswith('/api/routes/live')) else None)
                page.goto(BASE + '/routes/live')
                page.wait_for_selector('.lv-item')
                items = page.locator('.lv-item')
                check(items.count() == 4, f'в списке 4 машины ({items.count()})')
                check(page.locator('.lv-marker').count() == 4, 'на карте 4 маркера')
                states = page.eval_on_selector_all('.lv-item .lv-dot', 'els => els.map(e => e.className)')
                # машина 3: GPS выключен и связи нет — активная тревога GPS важнее «կապ չկա» (повторное ревью №76)
                check(sum('is-alert' in s for s in states) >= 3 and not any('is-offline' in s for s in states),
                      f'состояния: у машин 1-3 «ահազանգ», «կապ չկա» только без других тревог: {states}')
                probs = page.locator('#lvProbList button.lv-prob')
                check(probs.count() >= 3, f'«Խնդիրներ հիմա»: строки проблем ({probs.count()})')
                check('ուշանում' in page.inner_text('#lvProbList'), '«Խնդիրներ հիմա»: «не успеет» машины 1')
                first = page.inner_text(f'.lv-item[data-car="{cars[0]}"]')
                check('1 խանութ ուշանում է' in first, f'№87: в списке у машины 1 — «1 խանութ ուշանում է» ({first!r})')
                page.locator(f'.lv-item[data-car="{cars[0]}"]').click()
                page.wait_for_selector('#lvCard:not([hidden]) .lv-grid dt')
                page.wait_for_timeout(800)
                act1 = page.inner_text('#lvActive')
                check('Կուշանա պատուհանից' in act1 and 'րոպեով' in act1, f'№87: в карточке — «Կուշանա պատուհանից N րոպեով» ({act1!r})')
                labels = page.eval_on_selector_all('#lvGrid dt', 'els => els.map(e => e.textContent)')
                for need in ('Դիրքը', 'Արագություն', 'Վարորդ / առաքիչ', 'Խանութներ', 'Այսօր, կմ (GPS)', 'Վառելիք',
                             'Բեռի մնացորդ', 'Հաջորդ խանութը', 'Վերադարձ պահեստ', 'Վերջին կապը', 'Տերմինալ'):
                    check(need in labels, f'карточка: «{need}»')
                grid = page.inner_text('#lvGrid')
                check('2 / 3' in grid and '≈' in grid and 'տող առանց քաշի' in grid and 'APK 2.2.0' in grid,
                      'карточка: магазины 2/3, топливо ≈, строки без веса, версия APK')
                check('բեռնված այս երթում' in grid and 'վերադարձ՝' in grid and 'մեքենայում' in grid,
                      'этап 2: «загружено в этом рейсе», возврат на борту')
                check('լիցքավորում' in grid and '80' in grid, 'этап 2: сверка топлива с заправкой')
                check('(մոտավոր)' in grid or '(ճանապարհներով)' in grid, 'этап 2: источник ETA подписан')
                page.locator('#lvStopsBox summary').click()
                check('≈' in page.inner_text('#lvStops'), 'этап 2: у оставшегося магазина — ETA')
                check(page.locator('#lvStops tr.is-late').count() == 1, '№87: опаздывающий магазин отмечен в списке точек')
                check(page.locator('#lvStops tr').count() == 3 and 'ժամանում' in page.inner_text('#lvStops'),
                      'план — факт: 3 строки, у посещённых — прибытие и отъезд по GPS')
                check(page.is_visible('#lvReplay'), 'воспроизведение дня: есть у выбранной машины')
                page.eval_on_selector('#lvReplayRange', "r => { r.value = String((Number(r.min) + Number(r.max)) / 2); "
                                                        "r.dispatchEvent(new Event('input')); }")
                page.wait_for_timeout(300)
                check(page.is_visible('#lvReplayExit') and page.inner_text('#lvReplayTime') != '—',
                      f'воспроизведение: ползунок двигает время ({page.inner_text("#lvReplayTime")})')
                page.locator('#lvReplayPlay').click()
                page.wait_for_timeout(600)
                check('Դադար' in page.inner_text('#lvReplayPlay'), 'воспроизведение: «Նվագարկել» → «Դադար»')
                page.locator('#lvReplayExit').click()
                check(not page.is_visible('#lvReplayExit'), 'воспроизведение: «Փակել» — обратно к живой карте')
                check(page.locator('.leaflet-overlay-pane path').count() >= 4, 'путь и магазины выбранной машины на карте')
                page.locator('#lvLogBox summary').click()
                check('Արագության գերազանցում' in page.inner_text('#lvLog'), 'журнал: превышение скорости')
                page.screenshot(path=str(SHOTS / 'live_desktop.png'), full_page=False)
                page.locator(f'.lv-item[data-car="{cars[1]}"]').click()
                page.wait_for_timeout(1200)
                check('Երկար կանգառ' in page.inner_text('#lvActive'), 'машина 2: активная тревога «долгая стоянка»')
                page.screenshot(path=str(SHOTS / 'live_desktop_stop.png'))
                page.locator(f'.lv-item[data-car="{cars[2]}"]').click()
                page.wait_for_timeout(1200)
                act = page.inner_text('#lvActive')
                late = clock.now().hour >= live.NO_CONTACT_END_H   # после 20:00 «нет связи» — не тревога (ревью №76)
                check(('Կապ չկա' in act) != late and 'GPS' in act,
                      f'машина 3: «нет связи» {"не " if late else ""}тревога и «GPS выключен» ({act!r})')
                if not late:
                    third = page.inner_text(f'.lv-item[data-car="{cars[2]}"]')
                    check('կապ չկա' in third and 'ուշացում' not in third, f'машина 3: «կապ չկա N րոպե» без опоздания ({third!r})')
                    check(page.locator('.lv-marker.is-stale .lv-marker-last').count() >= 1,
                          'машина 3: маркер полупрозрачный, «վերջինը՝ HH:MM»')
                    check('Վերջին հայտնի դիրքը' in page.inner_text('#lvGrid'), 'машина 3: «Վերջին հայտնի դիրքը» в карточке')
                n = len(polls)
                page.wait_for_timeout(16000)
                check(len(polls) > n, f'опрос раз в 15 с ({n} → {len(polls)})')

                phone = browser.new_page(viewport={'width': 390, 'height': 860}, is_mobile=True, has_touch=True)
                phone.on('pageerror', lambda e: errors.append(str(e)))
                phone.goto(BASE + '/routes/live')
                phone.wait_for_selector('.lv-item')
                phone.locator(f'.lv-item[data-car="{cars[0]}"]').click()
                phone.wait_for_selector('#lvCard:not([hidden]) .lv-grid dt')
                phone.wait_for_timeout(800)
                wide = phone.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
                check(not wide, 'телефон: нет горизонтальной прокрутки')
                phone.screenshot(path=str(SHOTS / 'live_phone.png'), full_page=True)
                browser.close()
        finally:
            server.shutdown()
    check(not errors, 'нет ошибок страницы и консоли' + (f': {errors}' if errors else ''))
    print(f'{sum(results)}/{len(results)} OK; снимки — {SHOTS}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

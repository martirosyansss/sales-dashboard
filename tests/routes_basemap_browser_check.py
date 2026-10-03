# -*- coding: utf-8 -*-
"""Проверка подложки карт «Маршрутов» (static/js/routes_basemap.js, решение владельца №47) в настоящем браузере.

Запуск из корня проекта:  python tests/routes_basemap_browser_check.py
Имя без префикса test_: pytest его не собирает (нужны Playwright с Chromium и интернет — Leaflet с jsDelivr,
сценарий D ходит к настоящему Tiles API Яндекса с заведомо неверным ключом). Настоящий ключ не нужен:
ответы Яндекса в остальных сценариях подменяются. Сервер дашборда не запускается — только статика проекта.

A нет ключа → OpenStreetMap, тёмный фильтр routes.css на нём есть;
B ключ есть → плитки Яндекса с нужными параметрами, без фильтра (условия запрещают менять плитки),
  логотип в левом нижнем углу со ссылкой на Яндекс Карты, остальные карты страницы — тоже Яндекс;
C первая попытка каждой плитки — 429 → повтор (другой адрес той же плитки), всё загрузилось, без OSM;
D настоящий Яндекс, неверный ключ (403) → обе открытые карты страницы на OSM, логотип убран, предупреждение
  в консоли; карта, открытая после этого, — сразу на OSM;
E часть плиток не грузится, остальные грузятся → остаёмся на Яндексе.
"""
from __future__ import annotations

import base64
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
KEY = '0b6a3f1c-5d2e-4f7a-9c8b-1e2d3c4b5a69'
# серый квадрат 1×1 — подменная плитка
TILE_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGM4AQAAygDJmcpdfgAAAABJRU5ErkJggg==')
HARNESS = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="/css/routes.css">
<style>body{margin:0}#m{width:900px;height:500px}#m1,#m2{width:300px;height:200px}</style></head>
<body><div class="rt-page"><div id="m"></div><div id="m1"></div></div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="/js/routes_basemap.js?v=1" data-yandex-key="__KEY__"></script>
<script>
  const map = L.map('m', { zoomSnap: 0.5 });
  RoutesBasemap.add(map);
  map.setView([40.18, 44.51], 12);
  L.polyline([[40.17, 44.49], [40.19, 44.53]], { color: 'red', weight: 5 }).addTo(map);
  const m1 = L.map('m1'); RoutesBasemap.add(m1); m1.setView([40.2, 44.5], 13);   // вторая открытая карта
  setTimeout(() => {   // карта, открытая позже (как диалоги «Развоза»): неверный ключ к этому времени уже распознан
    const d = document.createElement('div'); d.id = 'm2'; document.querySelector('.rt-page').appendChild(d);
    const m2 = L.map('m2'); RoutesBasemap.add(m2); m2.setView([40.18, 44.51], 12);
  }, 6000);
</script></body></html>"""
STATE_JS = """() => {
    const m = document.getElementById('m'), m1 = document.getElementById('m1'), m2 = document.getElementById('m2');
    const kind = (el) => !el ? null : (el.querySelector('.rt-yandex-logo') ? 'yandex' : 'osm');
    const a = m.querySelector('.rt-yandex-logo');
    const box = a && a.getBoundingClientRect(), mb = m.getBoundingClientRect();
    const imgs = [...m.querySelectorAll('.leaflet-tile')];
    const layer = m.querySelector('.leaflet-tile-pane .leaflet-layer');
    return {
        logo: !!a, href: a && a.href, target: a && a.target,
        inCorner: !!a && Math.abs(box.left - mb.left) < 1 && Math.abs(box.bottom - mb.bottom) < 1,
        logoLoaded: !!a && a.querySelector('img').naturalWidth > 0,
        tiles: imgs.length, tilesOk: imgs.filter(i => i.complete && i.naturalWidth > 0).length,
        filter: layer ? getComputedStyle(layer).filter : null,
        paneFilter: getComputedStyle(m.querySelector('.leaflet-tile-pane')).filter,
        attribution: m.querySelector('.leaflet-control-attribution').textContent,
        m1: kind(m1), m2: kind(m2),
    };
}"""


def serve():
    # только static/ — в корне проекта лежит .env с паролями
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / 'static'))
    handler.log_message = lambda *a: None
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def run(pw, name, key, yandex=None, wait=7):
    """yandex: None — настоящий сервер Яндекса; иначе функция(url, попытка) -> HTTP-статус подменного ответа."""
    browser = pw.chromium.launch()
    page = browser.new_page(device_scale_factor=2)
    log = {'yandex': [], 'osm': 0, 'warn': [], 'errors': []}
    attempts = {}
    page.on('console', lambda m: log['warn'].append(m.text) if m.type == 'warning' else None)
    page.on('pageerror', lambda e: log['errors'].append(str(e)))
    page.route(f'http://127.0.0.1:{PORT}/__harness.html',
               lambda r: r.fulfill(status=200, content_type='text/html', body=HARNESS.replace('__KEY__', key)))

    def on_yandex(route):
        url = route.request.url
        log['yandex'].append(url)
        if yandex is None:
            return route.continue_()
        q = parse_qs(urlparse(url).query)
        tile = (q['x'][0], q['y'][0], q['z'][0])
        attempts[tile] = attempts.get(tile, 0) + 1
        status = yandex(url, attempts[tile])
        route.fulfill(status=status, content_type='image/png' if status == 200 else 'text/plain',
                      body=TILE_PNG if status == 200 else b'err')

    def on_osm(route):
        log['osm'] += 1
        route.fulfill(status=200, content_type='image/png', body=TILE_PNG)

    page.route('https://tiles.api-maps.yandex.ru/**', on_yandex)
    page.route('https://*.tile.openstreetmap.org/**', on_osm)
    page.goto(f'http://127.0.0.1:{PORT}/__harness.html')
    page.wait_for_timeout(wait * 1000)      # не time.sleep: синхронный Playwright иначе не обслуживает page.route
    state = page.evaluate(STATE_JS)
    browser.close()
    return log, state, attempts


def main() -> int:
    results = []

    def check(cond, msg):
        results.append(bool(cond))
        print(('OK   ' if cond else 'FAIL ') + msg)

    httpd = serve()
    try:
        with sync_playwright() as pw:
            log, st, _ = run(pw, 'a', '', yandex=lambda u, n: 200)
            check(not log['yandex'] and log['osm'] > 0 and not st['logo'], f'A no key → OSM ({log["osm"]} tiles), no logo')
            check('invert' in (st['filter'] or ''), f'A dark filter on the OSM layer: {st["filter"]}')
            check('OpenStreetMap' in st['attribution'] and not log['errors'], 'A attribution OSM, no JS errors')

            log, st, _ = run(pw, 'b', KEY, yandex=lambda u, n: 200)
            q = parse_qs(urlparse(log['yandex'][0]).query) if log['yandex'] else {}
            check(log['yandex'] and log['osm'] == 0, f'B Yandex tiles ({len(log["yandex"])}), no OSM')
            check(q.get('projection') == ['web_mercator'] and q.get('apikey') == [KEY] and q.get('scale') == ['2']
                  and q.get('l') == ['map'] and q.get('lang') == ['ru_RU'], f'B URL params {q}')
            check(st['filter'] == 'none' and st['paneFilter'] == 'none', f'B Yandex tiles unmodified: {st["filter"]}')
            check(st['tilesOk'] == st['tiles'] > 0, f'B all tiles loaded {st["tilesOk"]}/{st["tiles"]}')
            check(st['logo'] and st['href'] == 'https://yandex.ru/maps/' and st['target'] == '_blank'
                  and st['inCorner'] and st['logoLoaded'], 'B logo: bottom-left corner, link to Yandex Maps, loaded')
            check('Яндекс' in st['attribution'] and 'OpenStreetMap' not in st['attribution'], 'B attribution Яндекс')
            check(not log['errors'] and not log['warn'] and st['m1'] == st['m2'] == 'yandex',
                  'B no JS errors; other maps of the page Yandex too')

            log, st, att = run(pw, 'c', KEY, yandex=lambda u, n: 429 if n == 1 else 200)
            check(log['osm'] == 0 and st['logo'] and st['tilesOk'] == st['tiles'] > 0 and min(att.values()) >= 2,
                  f'C 429 → retried, {st["tilesOk"]}/{st["tiles"]} loaded, no fallback')

            log, st, _ = run(pw, 'd', KEY, yandex=None)
            retries = [u for u in log['yandex'] if '&scale=2.0&' in u or '&scale=2.00&' in u]
            check(log['osm'] > 0 and not st['logo'] and 'OpenStreetMap' in st['attribution'],
                  f'D real Yandex, bad key → OSM after {len(log["yandex"])} requests ({len(retries)} retries)')
            check(retries and all(u.replace('&scale=2.00&', '&scale=2&').replace('&scale=2.0&', '&scale=2&')
                                  in log['yandex'] for u in retries), 'D retries re-request the same tiles')
            check(any('ROUTES_YANDEX_TILES_KEY' in w for w in log['warn']) and not log['errors'],
                  'D console warning, no JS errors')
            check(st['m1'] == 'osm' and st['m2'] == 'osm', f'D open map m1 switched too ({st["m1"]}), '
                  f'map opened later straight to OSM ({st["m2"]})')

            log, st, _ = run(pw, 'e', KEY, yandex=lambda u, n: 500 if 'x=2554' in u else 200)
            check(log['osm'] == 0 and st['logo'], 'E some tiles fail, others load → stays on Yandex')
    finally:
        httpd.shutdown()
    print('ALL OK' if all(results) else 'SOME FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())

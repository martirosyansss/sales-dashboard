# -*- coding: utf-8 -*-
"""Подложка карт раздела «Маршруты» — Яндекс Карты через Tiles API (решение владельца №47).

Поведение в браузере (повторы, переход на OpenStreetMap, логотип, фильтр) — tests/routes_basemap_browser_check.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAP_PAGES = {'/routes': 'routes_overview', '/routes/settings': 'routes_settings',
             '/routes/optimize': 'routes_optimize', '/routes/dispatch': 'routes_dispatch',
             '/routes/learning': 'routes_learning'}
KEY = '0b6a3f1c-5d2e-4f7a-9c8b-1e2d3c4b5a69'


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
    import route_optimizer
    from route_optimizer import views

    class FakeDb:
        connection_string = 'DRIVER={none};'

    # шаблоны наследуют меню дашборда — здесь проверяем только то, что страница получает от сервера
    monkeypatch.setattr(views, 'render_template', lambda name, **ctx: json.dumps({'template': name, **ctx}))
    app = Flask(__name__)
    app.secret_key = 'test'
    route_optimizer.init_app(app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    return app.test_client()


def rendered(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    return json.loads(resp.get_data(as_text=True))


@pytest.mark.parametrize('path', sorted(MAP_PAGES))
def test_map_pages_get_tiles_key(client, monkeypatch, path):
    monkeypatch.setenv('ROUTES_YANDEX_TILES_KEY', f'  {KEY}\n')
    ctx = rendered(client, path)
    assert ctx == {'template': MAP_PAGES[path] + '.html', 'yandex_tiles_key': KEY}


@pytest.mark.parametrize('value', ['', '   ', 'abc', 'x" onload="alert(1)', KEY + ' extra', 'к' * 36, 'a' * 65])
def test_missing_or_malformed_key_means_osm(client, monkeypatch, value):
    monkeypatch.setenv('ROUTES_YANDEX_TILES_KEY', value)
    assert rendered(client, '/routes/dispatch')['yandex_tiles_key'] == ''


def test_no_env_means_osm(client, monkeypatch):
    monkeypatch.delenv('ROUTES_YANDEX_TILES_KEY', raising=False)
    assert rendered(client, '/routes')['yandex_tiles_key'] == ''


@pytest.mark.parametrize('page', sorted(MAP_PAGES.values()))
def test_templates_load_basemap_between_leaflet_and_page_script(page):
    html = (ROOT / 'templates' / f'{page}.html').read_text(encoding='utf-8')
    leaflet = html.index('leaflet@1.9.4/dist/leaflet.js')
    basemap = html.index("filename='js/routes_basemap.js'")
    own = html.index(f"filename='js/{page}.js'")
    assert leaflet < basemap < own
    tag = re.search(r'<script src="[^"]*routes_basemap\.js[^"]*"([^>]*)>', html)
    assert tag and tag.group(1).strip() == 'data-yandex-key="{{ yandex_tiles_key }}"'


def test_page_scripts_take_tiles_only_from_basemap():
    for js in sorted((ROOT / 'static' / 'js').glob('routes_*.js')):
        text = js.read_text(encoding='utf-8')
        if js.name == 'routes_basemap.js':
            continue
        assert 'tile.openstreetmap.org' not in text and 'L.tileLayer' not in text, js.name
        assert len(re.findall(r'\bL\.map\(', text)) == text.count('RoutesBasemap.add('), js.name


def test_basemap_follows_yandex_terms():
    js = (ROOT / 'static' / 'js' / 'routes_basemap.js').read_text(encoding='utf-8')
    url = re.search(r"'(https://tiles\.api-maps\.yandex\.ru/v1/tiles/[^']*)'\s*\+\s*'([^']*)'", js)
    assert url, 'Tiles API URL'
    params = dict(p.split('=') for p in (url.group(1) + url.group(2)).split('?')[1].split('&'))
    assert params['projection'] == 'web_mercator'     # иначе эллиптическая проекция — сдвиг относительно Leaflet
    assert params['l'] == 'map' and params['lang'] == 'ru_RU' and params['apikey'] == '{ymKey}'
    # логотип обязателен: в углу, ссылка на Яндекс Карты, картинка из официального набора
    assert "position: 'bottomleft'" in js and "'https://yandex.ru/maps/'" in js
    logo = ROOT / 'static' / 'img' / 'yandex_maps_logo_ru.svg'
    assert "'../img/yandex_maps_logo_ru.svg'" in js and logo.read_text(encoding='utf-8').startswith('<svg')


def test_dark_filter_never_touches_yandex_tiles():
    """Условия Tiles API запрещают изменять плитки (п. 5.1.2) — тёмный фильтр только на слое OpenStreetMap."""
    css = ''.join(re.sub(r'/\*.*?\*/', '', f.read_text(encoding='utf-8'), flags=re.S)
                  for f in sorted((ROOT / 'static' / 'css').glob('routes*.css')))
    filtered = [rule.split('{')[0].strip() for rule in re.findall(r'[^{}]+\{[^}]*\bfilter\s*:[^}]*\}', css)]
    assert not [s for s in filtered if 'leaflet-tile' in s or 'leaflet-layer' in s], filtered
    assert '.rt-page .rt-tiles-osm' in filtered
    js = (ROOT / 'static' / 'js' / 'routes_basemap.js').read_text(encoding='utf-8')
    assert js.count("className: 'rt-tiles-osm'") == 1          # только у слоя OpenStreetMap

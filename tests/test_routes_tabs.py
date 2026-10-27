"""Верхняя панель раздела «Маршруты» (Tesla+, решение владельца 11.10, вариант A «группы»: вместо меню слева №92 и тёмной
шапки дашборда). Одно место — templates/_routes_top.html через routes_base.html: на каждой странице раздела те же группы,
пункты и порядок, текущая группа и страница отмечены ровно один раз. Роли: администратор — все группы и разделы Sales
Dashboard; «Гараж» — плоско свои четыре страницы, «Склад» — свою; ссылок, которые сервер не откроет роли, нет.
Рендер — настоящий app_v2 (гейт, роли, шаблоны) с временной базой маршрутов (фикстура test_garage_public).
Поведение в браузере — tests/routes_top_browser_check.py.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_garage_public import LAN, _session_as, app_v2, client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ('Պլանավորում', [('/routes', 'Ակնարկ'), ('/routes/optimize', 'Օպտիմալացում'), ('/routes/learning', 'Ուսուցում')]),
    ('Առաքում', [('/routes/dispatch', 'Առաքում'), ('/routes/live', 'Մեքենաները առցանց')]),
    ('Թիմ', [('/routes/drivers', 'Վարորդներ'), ('/routes/araqich', 'Առաքիչների KPI'), ('/routes/garage', 'Ավտոտնակ'),
             ('/routes/refuels', 'Լիցքավորումներ')]),
    ('Ֆինանսներ', [('/routes/pay', 'Աշխատավարձ'), ('/routes/cost', 'Առաքման արժեք')]),
]
SETTINGS = ('/routes/settings', 'Կարգավորումներ')
LINKS = [x for _, items in GROUPS for x in items]
PAGES = {'routes_overview.html': '/routes', 'routes_optimize.html': '/routes/optimize',
         'routes_dispatch.html': '/routes/dispatch', 'routes_learning.html': '/routes/learning',
         'routes_settings.html': '/routes/settings', 'routes_garage.html': '/routes/garage',
         'routes_refuels.html': '/routes/refuels',
         'routes_pay.html': '/routes/pay', 'routes_araqich.html': '/routes/araqich',
         'routes_cost.html': '/routes/cost', 'routes_drivers.html': '/routes/drivers', 'routes_live.html': '/routes/live',
         'routes_warehouse.html': '/routes/warehouse'}
GARAGE_PAGES = ['/routes/garage', '/routes/refuels', '/routes/live', '/routes/drivers']
ITEM_RE = re.compile(r'<a class="rt-top-item" href="([^"]+)"( aria-current="page")?>\s*<span class="rt-top-ico" aria-hidden="true">'
                     r'<i class="fas fa-[\w-]+"></i></span>\s*<span class="rt-top-txt"><span class="rt-top-lbl">([^<]+)</span>'
                     r'<span class="rt-top-hint">[^<]+</span></span></a>')


def _page(client, url, who='boss'):
    _session_as(client, who, base=LAN)
    r = client.get(url, base_url=LAN)
    assert r.status_code == 200, (url, who, r.status_code)
    return r.get_data(as_text=True)


def _top(html):
    tops = re.findall(r'<header class="rt-page rt-top" id="rtTop" lang="hy">(.*?)</header>', html, flags=re.S)
    assert len(tops) == 1, f'ожидалась одна панель раздела, найдено {len(tops)}'
    return tops[0]


def test_every_page_template_uses_the_section_layout():
    """Все страницы раздела — через routes_base.html со своим адресом; меню слева и старой полосы вкладок нет."""
    for template, url in PAGES.items():
        src = (ROOT / 'templates' / template).read_text(encoding='utf-8')
        assert src.startswith('{% extends "routes_base.html" %}'), template
        assert f"{{% set rt_nav = '{url}' %}}" in src, template
    assert not (ROOT / 'templates' / '_routes_side.html').exists() and not (ROOT / 'static' / 'js' / 'routes_side.js').exists()
    css = (ROOT / 'static' / 'css' / 'routes.css').read_text(encoding='utf-8')
    assert 'rt-secnav' not in css and '.rt-shell' not in css and 'rt-tabs' not in css and 'rt-secbar' not in css


def test_guard_new_routes_pages_use_the_layout():
    """Сторож: новая страница раздела (templates/routes_*.html) — тоже через routes_base.html (иначе останется без панели)."""
    for path in sorted((ROOT / 'templates').glob('routes_*.html')):
        if path.name == 'routes_base.html':
            continue
        assert path.read_text(encoding='utf-8').startswith('{% extends "routes_base.html" %}'), path.name
    for path in sorted((ROOT / 'templates').rglob('*.html')):
        src = path.read_text(encoding='utf-8')
        assert 'rt-secnav' not in src and 'rtShell' not in src and 'rt-tabs' not in src, path.name


@pytest.mark.parametrize('url', sorted(set(PAGES.values()) - {'/routes/warehouse'}))
def test_admin_top_bar_same_groups_and_one_current(client, url):
    html = _page(client, url)
    top = _top(html)
    # группы: кнопки-раскрывашки с выпадашкой; текущая группа — своя
    pills = re.findall(r'<button type="button" class="rt-top-pill( is-current)?" id="(rtTopG\d)" data-rt-pop\s+'
                       r'aria-expanded="false" aria-controls="(rtTopG\dPanel)">([^<]+)<svg', top)
    assert [label for _, _, _, label in pills] == [g for g, _ in GROUPS]
    assert all(ctrl == pid + 'Panel' and f'id="{ctrl}" hidden' in top for _, pid, ctrl, _ in pills)
    cur = [label for on, _, _, label in pills if on]
    assert cur == [g for g, items in GROUPS if url in dict(items)]
    # пункты выпадашек: те же адреса и порядок, текущий — ровно своя страница
    panels = re.search(r'<nav class="rt-top-nav".*?</nav>', top, flags=re.S).group(0)
    items = ITEM_RE.findall(panels)
    assert [(h, t) for h, _, t in items] == LINKS
    assert [h for h, c, _ in items if c] == ([url] if url != SETTINGS[0] else [])
    # ⚙ — настройки раздела, текущая на своей странице
    gear = re.search(r'<a class="rt-top-iconbtn" href="/routes/settings" aria-label="Կարգավորումներ" title="Կարգավորումներ"'
                     r'( aria-current="page")?>', top)
    assert gear and bool(gear.group(1)) == (url == SETTINGS[0])
    # лист узкого экрана: все группы и настройки; кнопка — с названием текущей страницы
    sheet = re.search(r'<div class="rt-top-panel rt-top-sheet" id="rtTopSheet" hidden>(.*?)</div>\s*</header>', html, flags=re.S)
    assert [(h, t) for h, _, t in ITEM_RE.findall(sheet.group(1))] == LINKS + [SETTINGS]
    here = dict(LINKS + [SETTINGS])[url]
    assert f'<span class="rt-top-menu-v">{here}</span>' in top
    # разделы Sales Dashboard — те же адреса, что у шапки дашборда; своей тёмной шапки и подвала на странице нет
    for href in ('/', '/settings', '/managers', '/customers-grid', '/production', '/routes', '/courier', '/reports', '/ai-assistant'):
        assert f'<a class="rt-top-link" href="{href}">' in top, href
    assert 'app-nav' not in html and 'READ-ONLY Mode' not in html and 'gj-nav' not in html
    assert 'js/routes_top.js' in html and 'rt-secnav' not in html


@pytest.mark.parametrize('url', GARAGE_PAGES)
def test_garage_role_sees_only_its_pages(client, url):
    html = _page(client, url, 'garage1')
    top = _top(html)
    pills = re.findall(r'<a class="rt-top-pill" href="([^"]+)"( aria-current="page")?>([^<]+)</a>', top)
    assert [h for h, _, _ in pills] == GARAGE_PAGES and [h for h, c, _ in pills if c] == [url]
    hrefs = set(re.findall(r'href="([^"]+)"', top))
    assert hrefs == set(GARAGE_PAGES)                       # ни настроек, ни разделов дашборда, ни чужих страниц
    assert 'rtTopApps' not in top and 'data-rt-pop aria-expanded="false" aria-controls="rtTopG' not in top
    assert 'action="/logout"' in top and 'Ելք' in top and re.search(r'class="rt-page[^"]*" id=', html)
    assert 'app-nav' not in html and 'gj-nav' not in html


def test_garage_role_links_all_open(client):
    """Каждая ссылка панели «Гаража» сервер действительно открывает (не 403 и не переход на журнал)."""
    _session_as(client, 'garage1', base=LAN)
    for url in GARAGE_PAGES:
        r = client.get(url, base_url=LAN)
        assert r.status_code == 200, url


def test_warehouse_role_sees_only_its_page(client, app_v2):
    from werkzeug.security import generate_password_hash
    app_v2.test_users['wh1'] = {'role': 'warehouse', 'areas': [], 'display_name': 'Պահեստ',
                                'password_hash': generate_password_hash('warehouse-pass-1', method='pbkdf2:sha256:1000')}
    html = _page(client, '/routes/warehouse', 'wh1')
    top = _top(html)
    assert set(re.findall(r'href="([^"]+)"', top)) == {'/routes/warehouse'}
    assert '<a class="rt-top-pill" href="/routes/warehouse" aria-current="page">Պահեստ</a>' in top
    assert 'rtTopMenu' not in top and 'rtTopApps' not in top    # одна страница — без листа и без разделов дашборда
    assert 'action="/logout"' in top and 'id="whPage"' in html


def test_admin_warehouse_page_has_full_top_bar(client):
    top = _top(_page(client, '/routes/warehouse'))
    assert 'rtTopApps' in top and '<span class="rt-top-menu-v">Պահեստ</span>' in top
    assert 'aria-current="page"' not in re.search(r'<nav class="rt-top-nav".*?</nav>', top, flags=re.S).group(0)

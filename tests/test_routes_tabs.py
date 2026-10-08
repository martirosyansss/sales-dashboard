"""Меню раздела «Маршруты» слева (владелец 08.10: полоса из десяти одинаковых вкладок — «очень не профессионально» →
меню слева с группами, как у Routific / Onfleet). Одно место — templates/_routes_side.html через routes_base.html:
на каждой странице раздела те же пункты в том же порядке и группах, текущий отмечен ровно один — своя страница.
«Ավտոտնակ», «Մեքենաները առցանց» и «Վարորդներ» открывает и роль «Гараж» — ей меню нет, как и раньше.
Рендер — настоящий app_v2 (гейт, роли, шаблоны) с временной базой маршрутов (фикстура test_garage_public).
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
    ('Թիմ', [('/routes/drivers', 'Վարորդներ'), ('/routes/araqich', 'Առաքիչների KPI'), ('/routes/garage', 'Ավտոտնակ')]),
    ('Ֆինանսներ', [('/routes/pay', 'Աշխատավարձ'), ('/routes/cost', 'Առաքման արժեք')]),
]
SETTINGS = ('/routes/settings', 'Կարգավորումներ')
LINKS = [x for _, items in GROUPS for x in items] + [SETTINGS]
PAGES = {'routes_overview.html': '/routes', 'routes_optimize.html': '/routes/optimize',
         'routes_dispatch.html': '/routes/dispatch', 'routes_learning.html': '/routes/learning',
         'routes_settings.html': '/routes/settings', 'routes_garage.html': '/routes/garage',
         'routes_pay.html': '/routes/pay', 'routes_araqich.html': '/routes/araqich',
         'routes_cost.html': '/routes/cost', 'routes_drivers.html': '/routes/drivers', 'routes_live.html': '/routes/live'}
GARAGE_PAGES = {'/routes/garage', '/routes/live', '/routes/drivers'}
LINK_RE = re.compile(r'<a class="rt-secnav-link" href="([^"]+)"( aria-current="page")?><i class="fas fa-[\w-]+" aria-hidden="true">'
                     r'</i><span class="rt-secnav-t">([^<]+)</span></a>')


def _page(client, url, who='boss'):
    _session_as(client, who, base=LAN)
    r = client.get(url, base_url=LAN)
    assert r.status_code == 200, (url, who, r.status_code)
    return r.get_data(as_text=True)


def _menu(html):
    navs = re.findall(r'<nav class="rt-secnav-nav" aria-label="Երթուղիների բաժիններ">(.*?)</nav>', html, flags=re.S)
    assert len(navs) == 1, f'ожидалось одно меню раздела, найдено {len(navs)}'
    return navs[0]


def test_every_page_template_uses_the_section_layout():
    """Все 11 страниц — через routes_base.html со своим адресом; старой полосы вкладок нигде нет."""
    assert sorted(PAGES.values()) == sorted(url for url, _ in LINKS)
    for template, url in PAGES.items():
        src = (ROOT / 'templates' / template).read_text(encoding='utf-8')
        assert src.startswith('{% extends "routes_base.html" %}'), template
        assert f"{{% set rt_nav = '{url}' %}}" in src, template
    for css in ('routes.css', 'routes_dispatch.css'):
        assert 'rt-tabs' not in (ROOT / 'static' / 'css' / css).read_text(encoding='utf-8')
        assert 'rt-secbar' not in (ROOT / 'static' / 'css' / css).read_text(encoding='utf-8')


def test_guard_new_routes_pages_use_the_layout_and_no_old_tabs():
    """Сторож: новая страница раздела (templates/routes_*.html) — тоже через routes_base.html (иначе останется без меню);
    старой полосы вкладок (.rt-tabs / .rt-secbar) нет ни в одном шаблоне."""
    not_section = {'routes_base.html', 'routes_warehouse.html'}   # каркас; «Склад» (№78) — своя страница роли, без меню
    for path in sorted((ROOT / 'templates').glob('routes_*.html')):
        if path.name in not_section:
            continue
        assert path.read_text(encoding='utf-8').startswith('{% extends "routes_base.html" %}'), path.name
    for path in sorted((ROOT / 'templates').rglob('*.html')):
        src = path.read_text(encoding='utf-8')
        assert 'rt-tabs' not in src and 'rt-secbar' not in src, path.name


@pytest.mark.parametrize('url', sorted(PAGES.values()))
def test_menu_on_every_page_same_links_groups_and_one_current(client, url):
    html = _page(client, url)
    nav = _menu(html)
    links = LINK_RE.findall(nav)
    assert [(href, text) for href, _, text in links] == LINKS
    assert [href for href, cur, _ in links if cur] == [url]
    side = re.search(r'<aside class="rt-page rt-secnav" id="rtSecNav" lang="hy">.*?</aside>', html, flags=re.S).group(0)
    assert side.count('aria-current="page"') == 1   # (у шапки дашборда — свой текущий пункт)
    # группы: подпись и её пункты; «Կարգավորումներ» — отдельно внизу
    grps = re.findall(r'<p class="rt-secnav-glabel" id="(rtSecNavG\d)">([^<]+)</p>\s*<ul class="rt-secnav-list" aria-labelledby="(\w+)">'
                      r'(.*?)</ul>', nav, flags=re.S)
    assert [(label, [(h, t) for h, _, t in LINK_RE.findall(body)]) for _, label, _, body in grps] == GROUPS
    assert all(gid == ref for gid, _, ref, _ in grps)
    foot = re.search(r'<ul class="rt-secnav-list rt-secnav-foot">(.*?)</ul>', nav, flags=re.S)
    assert foot and [(h, t) for h, _, t in LINK_RE.findall(foot.group(1))] == [SETTINGS]
    # каркас: меню слева, страница справа; на узком экране — кнопка с названием текущей страницы
    shell = html.index('<div class="rt-shell" id="rtShell"')
    # «Առաքում»: уже 1400 px меню сначала свёрнуто (пока пользователь сам не выбрал) — только у неё
    assert ('data-compact-below="1400"' in html) == (url == '/routes/dispatch')
    assert shell < html.index('id="rtSecNav"') < html.index('<div class="rt-shell-main">')
    here = dict(LINKS)[url]
    assert re.search(r'<span class="rt-secnav-open-v">' + re.escape(here) + '</span>', html)
    assert 'js/routes_side.js' in html


@pytest.mark.parametrize('url', sorted(GARAGE_PAGES))
def test_garage_role_sees_these_pages_without_menu(client, url):
    html = _page(client, url, 'garage1')
    assert 'rt-secnav' not in html and 'rtShell' not in html and 'routes_side.js' not in html
    assert 'class="rt-page" id=' in html   # сама страница на месте

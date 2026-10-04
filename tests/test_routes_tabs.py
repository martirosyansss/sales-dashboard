"""Вкладки раздела «Маршруты»: на каждой странице — одни и те же вкладки в одном порядке, текущая отмечена.

На «Առաքում» после редизайна пропала вкладка «Ավտոտնակ» (владелец: «առաքումը ընտրելուց ավտոտնակը կորում է»).
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABS = [('/routes', 'Ակնարկ'), ('/routes/optimize', 'Օպտիմալացում'), ('/routes/dispatch', 'Առաքում'),
        ('/routes/learning', 'Ուսուցում'), ('/routes/settings', 'Կարգավորումներ'), ('/routes/garage', 'Ավտոտնակ')]
PAGES = {'routes_overview.html': '/routes', 'routes_optimize.html': '/routes/optimize',
         'routes_dispatch.html': '/routes/dispatch', 'routes_learning.html': '/routes/learning',
         'routes_settings.html': '/routes/settings', 'routes_garage.html': '/routes/garage'}


def _tabs(template):
    html = (ROOT / 'templates' / template).read_text(encoding='utf-8')
    navs = re.findall(r'<nav class="rt-tabs"[^>]*>(.*?)</nav>', html, flags=re.S)
    assert len(navs) == 1, f'{template}: ожидалась одна <nav class="rt-tabs">, найдено {len(navs)}'
    return re.findall(r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', navs[0])


@pytest.mark.parametrize('template, current', sorted(PAGES.items()))
def test_every_routes_page_has_all_tabs(template, current):
    links = _tabs(template)
    assert [(href, text) for href, _, text in links] == TABS
    assert [href for href, cur, _ in links if cur] == [current]


@pytest.mark.parametrize('template', sorted(PAGES))
def test_tabs_sit_in_the_same_section_bar_on_every_page(template):
    """Владелец: «на каждом экране показывает по-разному» — вкладки стояли то в шапке справа, то под заголовком, то вне
    шапки. Теперь на всех страницах — одна полоса .rt-secbar сразу в начале контента, до самой страницы и её шапки."""
    html = (ROOT / 'templates' / template).read_text(encoding='utf-8')
    content = html.split('{% block content %}', 1)[1]
    bar = re.search(r'<div class="rt-page rt-secbar" lang="hy">\s*<nav class="rt-tabs"[^>]*>.*?</nav>\s*</div>', content, re.S)
    assert bar, f'{template}: вкладки не в общей полосе .rt-secbar'
    page = re.search(r'<div class="rt-page" id="\w+"', content)
    assert page and bar.end() < page.start(), f'{template}: полоса вкладок должна идти до страницы'
    before = content[:bar.start()].replace('{% if is_admin %}', '')
    assert re.fullmatch(r'\s*(<!--.*?-->\s*)*', before, re.S), f'{template}: перед полосой вкладок ничего не должно быть'

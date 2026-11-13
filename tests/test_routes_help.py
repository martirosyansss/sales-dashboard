"""«Ուղեցույց» (/routes/help): страница открывается администратору, все снимки на месте, оглавление ведёт на свои разделы;
значок «?» в верхней панели — только у администратора, другие роли страницу не получают (гейт app_v2 по умолчанию запрещает)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_garage_public import LAN, _session_as, app_v2, client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
LINK = 'href="/routes/help" aria-label="Ուղեցույց"'


def _get(client, who, url='/routes/help'):
    _session_as(client, who, base=LAN)
    return client.get(url, base_url=LAN)


def test_admin_opens_the_guide_with_every_screenshot(client):
    response = _get(client, 'boss')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<h1 class="rt-pagehead__title">Ուղեցույց</h1>' in html and 'css/routes_help.css' in html
    shots = set(re.findall(r'img/routes_help/([\w-]+\.jpg)', html))
    assert len(shots) == 12
    assert all((ROOT / 'static' / 'img' / 'routes_help' / name).is_file() for name in shots)
    anchors = re.findall(r'href="#([\w-]+)"', html)
    assert len(anchors) >= 9 and all(f'id="{anchor}"' in html for anchor in anchors)
    assert '<script' not in html.split('id="helpPage"', 1)[1].rsplit('</article>', 1)[0]   # только текст и снимки


def test_top_bar_links_the_guide_and_marks_it_current(client):
    html = _get(client, 'boss').get_data(as_text=True)
    assert LINK + ' title="Ուղեցույց" aria-current="page"' in html
    other = _get(client, 'boss', '/routes/learning').get_data(as_text=True)
    assert LINK + ' title="Ուղեցույց">' in other


@pytest.mark.parametrize('who, home', [('garage1', '/routes/garage'), ('wh1', '/routes/warehouse')])
def test_other_roles_get_neither_the_page_nor_the_link(client, app_v2, monkeypatch, who, home):
    from werkzeug.security import generate_password_hash
    monkeypatch.setitem(app_v2.test_users, 'wh1', {'role': 'warehouse', 'areas': [], 'display_name': 'Պահեստ',
                        'password_hash': generate_password_hash('warehouse-pass-1', method='pbkdf2:sha256:1000')})
    response = _get(client, who)
    assert response.status_code in (302, 403)
    assert 'routes_help' not in response.get_data(as_text=True)
    assert LINK not in _get(client, who, home).get_data(as_text=True)


def test_dispatch_page_has_one_help_the_top_bar_icon(client):
    """13.11.2026: своя кнопка «Օգնություն» и старая подсказка страницы «Առաքում» убраны — помощь одна, значок «?»."""
    html = _get(client, 'boss', '/routes/dispatch').get_data(as_text=True)
    assert LINK in html
    assert 'id="dpHelpBtn"' not in html and 'id="dpHelp"' not in html and 'Օգնություն' not in html
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'dpHelpBtn' not in js and "$('dpHelp')" not in js

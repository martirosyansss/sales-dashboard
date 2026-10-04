# -*- coding: utf-8 -*-
"""Шапка base_v2.html: страницы сгруппированы в разделы, раздел открытой страницы подсвечен, роли видят своё."""
import re
from pathlib import Path

import pytest
from flask import Flask, render_template_string

ROOT = Path(__file__).resolve().parents[1]
PAGE = '{% extends "base_v2.html" %}{% block content %}{% endblock %}'


def render(path, role):
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.add_url_rule('/logout', 'logout', lambda: '', methods=['POST'])
    app.context_processor(lambda: {'is_admin': role == 'admin', 'is_garage': role == 'garage',
                                   'current_user': None, 'current_username': 'qa', 'csrf_token': lambda: 'x'})
    with app.test_request_context(path):
        return render_template_string(PAGE)


def active(html):
    """Подписи подсвеченных пунктов шапки (разделы и страницы в выпадашках)."""
    found = re.findall(r'class="(?:nav-link|dropdown-item)[^"]*\bactive\b[^"]*"[^>]*>\s*<i[^>]*></i>([^<]+)<', html)
    return [s.strip() for s in found]


@pytest.mark.parametrize('path, expected', [
    ('/', ['Dashboard']),
    ('/routes/dispatch', ['Логистика', 'Маршруты']),
    ('/courier', ['Логистика', 'Առաքիչ']),
    ('/customers', ['Клиенты', 'Клиенты и долги']),       # точное совпадение: /customers-grid не задевает
    ('/customers-grid', ['Клиенты', 'Клиенты Grid']),
    ('/plans', ['Продажи', 'Планы']),
    ('/reports', ['Инструменты', 'Отчеты']),
])
def test_admin_section_of_open_page_is_highlighted(path, expected):
    assert active(render(path, 'admin')) == expected


def test_admin_sees_every_page_once():
    html = render('/', 'admin')
    for href in ('/managers', '/managers-kpi', '/quantity', '/groups', '/distributors', '/plans', '/customers',
                 '/customers-grid', '/customer-cards', '/areas', '/production', '/routes', '/courier', '/reports',
                 '/dashboard-builder', '/ai-assistant', '/settings'):
        assert html.count(f'href="{href}"') == 1, href


def test_territory_user_sees_only_own_pages():
    html = render('/areas', 'user')
    assert 'dropdown-item' not in html.split('<!-- Пользователь / выход -->')[0]
    assert 'href="/customers-grid"' in html and 'href="/areas"' in html
    assert 'href="/routes"' not in html and 'href="/settings"' not in html
    assert active(html) == ['Территории']


def test_garage_sees_no_pages_but_can_log_out():
    html = render('/routes/garage', 'garage')
    for href in ('/customers-grid', '/areas', '/routes"', '/settings'):
        assert f'href="{href}' not in html
    assert 'app-nav' not in html                                  # у «Гаража» своя короткая шапка (№53)
    assert 'gj-logout' in html and 'action="/logout"' in html

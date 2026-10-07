# -*- coding: utf-8 -*-
"""«Развоз»: ход дня на шкале (ответ владельца №82, как мониторинг Яндекса / Routific live). GET
/api/routes/dispatch/progress — по факту терминала «Առաքիչ» и ETA онлайн-карты (№76): магазин доставлен, частично,
отказ, машина на месте, опаздывает (ETA позже плана на PROGRESS_LATE_MIN), ждёт. Только сегодня.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_progress.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import views  # noqa: E402
from test_route_live import LAN, _session_as, app_v2, client, live_app  # noqa: E402,F401


def test_progress_marks_store_where_truck_stands_and_waiting_store(client, live_app):
    _session_as(client, 'boss', base=LAN)
    r = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['live'] is True and body['now'] == '11:00'
    car = body['trucks']['CAR1']
    assert set(car) == {'7', '8'}                                   # клиенты точек терминала — ключи строкой
    assert car['7']['s'] == 'here'                                  # машина стоит у магазина 7 (GPS), он ещё открыт
    assert car['8']['s'] in ('pending', 'late') and car['8']['at']  # ждёт: время — ETA онлайн-карты
    assert 'CAR9' not in body['trucks']                             # машина плана без терминала — не красим


def test_progress_late_and_delivered(client, live_app, monkeypatch):
    _session_as(client, 'boss', base=LAN)
    facts = live_app.app.extensions['route_optimizer'].live_facts.data['2026-10-03']['CAR1']
    facts['stops'][0]['status'] = 'full'
    facts['stops'][0]['delivered_at'] = '2026-10-03T10:50:00+04:00'
    live_app.app.extensions['route_optimizer'].live_facts.data['2026-10-03'] = dict(
        live_app.app.extensions['route_optimizer'].live_facts.data['2026-10-03'])   # новый факт — пересчёт кэша
    monkeypatch.setattr(views, 'PROGRESS_LATE_MIN', -10_000)       # любое ETA — «опаздывает»
    car = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['trucks']['CAR1']
    assert car['7'] == {'s': 'done', 'at': '10:50', 'delay': None}
    assert car['8']['s'] == 'late' and isinstance(car['8']['delay'], int)


def test_progress_only_today_and_with_courier(client, live_app, monkeypatch):
    _session_as(client, 'boss', base=LAN)
    assert client.get('/api/routes/dispatch/progress?date=2026-10-02', base_url=LAN).get_json() == \
        {'success': True, 'live': False, 'trucks': {}}
    assert client.get('/api/routes/dispatch/progress?date=03.10', base_url=LAN).status_code == 400
    monkeypatch.setattr(live_app.app.extensions['route_optimizer'], 'live_facts', None)
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['live'] is False

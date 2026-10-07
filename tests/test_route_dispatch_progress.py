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


def test_progress_access_admin_only_and_not_public(client, live_app):
    """Ход дня — данные логиста: пользователь с зоной и гараж — 403, интернет-хост (araqich.orix.am) — не отдаёт."""
    from test_garage_public import PUBLIC
    for who in ('u', 'garage1'):
        _session_as(client, who, base=LAN)
        assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).status_code == 403, who
    _session_as(client, 'boss', base=PUBLIC)
    assert client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=PUBLIC).status_code in (403, 404)


def test_progress_worst_state_of_several_invoices_and_here_over_late(client, live_app, monkeypatch):
    """Магазин с двумя накладными: одна доставлена, другая ждёт — магазин «ждёт» (худшее); машина у магазина — «here»
    даже при опоздании."""
    _session_as(client, 'boss', base=LAN)
    st = live_app.app.extensions['route_optimizer']
    fleet = st.live_facts.data['2026-10-03']
    stops = fleet['CAR1']['stops']
    twin = dict(stops[1], stop_id='S:B2', status='full', delivered_at='2026-10-03T10:30:00+04:00')
    fleet['CAR1'] = dict(fleet['CAR1'], stops=[*stops, twin])
    st.live_facts.data['2026-10-03'] = dict(fleet)
    monkeypatch.setattr(views, 'PROGRESS_LATE_MIN', -10_000)
    car = client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN).get_json()['trucks']['CAR1']
    assert car['8']['s'] == 'late'                                  # не «done» второй накладной
    assert car['7']['s'] == 'here'                                  # у магазина — «here», не «late»


def test_progress_cached_per_fleet_computation(client, live_app, monkeypatch):
    _session_as(client, 'boss', base=LAN)
    calls = []
    real = views._live_card
    monkeypatch.setattr(views, '_live_card', lambda *a, **k: calls.append(a[3]) or real(*a, **k))
    views._PROGRESS_CACHE.clear()
    client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN)
    n = len(calls)
    client.get('/api/routes/dispatch/progress?date=2026-10-03', base_url=LAN)
    assert n >= 1 and len(calls) == n                               # тот же расчёт флота — без пересчёта

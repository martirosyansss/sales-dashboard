# -*- coding: utf-8 -*-
"""Тревоги карты машин, круг 2 (владелец 08.10 «fix all»): общая «Տեսա» на сервере — схема 28 (live_ack после таблиц
Telegram-бота схемы 27), Store.live_ack_put / live_acks, POST /api/routes/live/ack, acks в ответах карты.

Базы — только временные копии: схемы 26 и 27 — созданные кодом и откатанные, база владельца — копия через sqlite backup
(источник открыт только для чтения). Эскалация в Telegram — у бота (live_alerts, tg_bot; №91), не здесь.
Запуск:  python -m pytest tests/test_route_live_ack.py -q
"""
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import store as st  # noqa: E402
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_live import live_app  # noqa: E402,F401

OWNER_DB = Path('F:/New Softs/Sales Dashboard/route_optimizer.db')   # база ПК владельца — только чтение, только копия


def _version_tables(path):
    with closing(sqlite3.connect(path)) as conn:
        v = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    return v, counts


def _ro_copy(src, dst):
    """Копия базы через sqlite backup; источник открыт только для чтения."""
    with closing(sqlite3.connect(f'file:{Path(src).as_posix()}?mode=ro', uri=True)) as x,             closing(sqlite3.connect(dst)) as y:
        x.backup(y)


# ============================== база: схема 28 ==============================

@pytest.mark.parametrize('old', [26, 27])
def test_schema_28_migrates_copy_of_old_schema_and_acks_roundtrip(tmp_path, old):
    """База схемы 26 (до бота) — 26 → 27 (бот) → 28; схемы 27 (ПК после бота) — 27 → 28. Прежние строки целы."""
    src = str(tmp_path / f'v{old}.db')
    s = st.Store(src)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    a, b = '2026-10-03T10:00:00+04:00', '2026-10-03T10:12:00+04:00'
    s.add_live_explanation('2026-10-03', 'CAR1', 'deviation', a, b, 'road', 'խցանում', 'boss')
    with closing(sqlite3.connect(src)) as conn:   # шаги 26 → 27 и 27 → 28 только добавляют таблицы
        conn.execute('DROP TABLE live_ack')
        if old == 26:
            conn.execute('DROP TABLE tg_message')
            conn.execute('DROP TABLE tg_kv')
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(old),))
        conn.commit()
    copy = str(tmp_path / 'copy.db')
    _ro_copy(src, copy)
    v0, before = _version_tables(copy)
    assert v0 == str(old) and 'live_ack' not in before and ('tg_message' in before) == (old == 27)
    s2 = st.Store(copy)
    assert s2.live_acks('2026-10-03') == []
    v1, after = _version_tables(copy)
    new = {'live_ack': 0} | ({'tg_message': 0, 'tg_kv': 0} if old == 26 else {})
    assert v1 == str(st.SCHEMA_VERSION) == '28' and after == {**before, **new}   # прежние строки целы
    assert st._MIGRATIONS[27] == (st._LIVE_ACK_TABLE,)
    assert st._MIGRATIONS[26] == (st._TG_MESSAGE_TABLE, st._TG_MESSAGE_INDEX, st._TG_KV_TABLE)   # шаг бота — как в trunk
    assert s2.truck_drivers('2026-10-02')[0] == {'CAR1': 'Արամ'}
    assert s2.live_explanations('2026-10-03')['CAR1'][0]['note'] == 'խցանում'
    assert _version_tables(src)[0] == str(old)   # источник не тронут
    # запись: строка на (день, машина, вид) — новая отметка заменяет случай, кто и когда
    s2.live_ack_put('2026-10-03', [('CAR1', 'speed', a), ('CAR1', 'late:window', None)], 'boss', a)
    s2.live_ack_put('2026-10-03', [('CAR1', 'speed', b)], 'garage1', b)
    assert s2.live_acks('2026-10-03') == [_ack('CAR1', 'late:window', None, 'boss', at=a),
                                          _ack('CAR1', 'speed', b, 'garage1', at=b)]
    s2.live_ack_put('2026-10-03', [('CAR1', 'speed', a)], 'garage2', '2026-10-03T10:30:00+04:00', refresh=True)
    assert s2.live_acks('2026-10-03')[1] == _ack('CAR1', 'speed', a, 'garage1', at=b, seen_at='2026-10-03T10:30:00+04:00')
    assert s2.live_acks('2026-10-04') == []
    for bad in ([('', 'speed', None)], [('CAR1', 'Speed', None)], [('CAR1', 'speed', '10:00')], []):
        with pytest.raises(ValueError):
            s2.live_ack_put('2026-10-03', bad, 'boss', a)
    with pytest.raises(ValueError):
        s2.live_ack_put('03.10.2026', [('CAR1', 'speed', None)], 'boss', a)


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы ПК владельца')
def test_schema_28_migrates_copy_of_owner_db(tmp_path):
    """Копия настоящей базы владельца (sqlite backup, источник — только чтение; ПК уже на 27 после бота): миграция до 28
    не теряет ни строки."""
    copy = tmp_path / 'owner.db'
    _ro_copy(OWNER_DB, copy)
    v0, before = _version_tables(copy)
    if int(v0) > st.SCHEMA_VERSION:
        pytest.skip(f'база владельца новее кода (схема {v0})')
    s = st.Store(str(copy))
    s.load()
    v1, after = _version_tables(copy)
    assert v1 == str(st.SCHEMA_VERSION) and {k: after[k] for k in before} == before and after['live_ack'] == 0
    s.live_ack_put('2026-10-08', [('333DN33', 'deviation', '2026-10-08T12:15:00+04:00')], 'qa', '2026-10-08T12:20:00+04:00')
    assert s.live_acks('2026-10-08')[-1]['user'] == 'qa'


# ============================== API «Տեսա» ==============================

URL = '/api/routes/live/ack'
SINCE = '2026-10-03T10:40:00+04:00'
AT = '2026-10-03T11:00:00+04:00'   # test_route_live.API_NOW


def _ack(car, key, since, user, at=AT, seen_at=None):
    return {'car': car, 'key': key, 'since': since, 'user': user, 'at': at, 'seen_at': seen_at or at}


def test_api_ack_validation_roles_today_batch_and_acks_in_get(client, live_app):
    state = live_app.app.extensions['route_optimizer']
    item = {'car': 'CAR1', 'key': 'speed', 'since': SINCE}
    h = _session_as(client, 'boss', base=LAN)
    r = client.post(URL, json={'items': [item]}, base_url=LAN)                       # без токена формы
    assert r.status_code == 403 and r.get_json()['error'] == 'csrf'
    assert client.post(URL, data='x', content_type='text/plain', base_url=LAN, headers=h).status_code == 415
    assert client.post(URL, json=[item], base_url=LAN, headers=h).status_code == 400
    for bad, field in (({'items': []}, 'items'), ({'items': [item] * 51}, 'items'), ({'items': 'x'}, 'items'),
                       ({'items': [{**item, 'car': ''}]}, 'items'), ({'items': [{**item, 'car': 'X' * 21}]}, 'items'),
                       ({'items': [{**item, 'key': 'Speed!'}]}, 'items'), ({'items': [{**item, 'since': 'x'}]}, 'items'),
                       ({'items': [{**item, 'since': '2026-10-03T10:40:00'}]}, 'items'),   # без пояса
                       ({'items': [item], 'date': '2026-10-02'}, 'date'), ({'items': [item], 'date': '03.10.2026'}, 'date'),
                       ({'items': [item], 'refresh': 'yes'}, 'refresh')):
        r = client.post(URL, json=bad, base_url=LAN, headers=h)
        assert r.status_code == 400 and field in r.get_json()['errors'], bad
    assert state.store.live_acks('2026-10-03') == []
    # 50 — можно; машины не из сегодняшнего парка карты (CAR1, CAR9) отбрасываются; повтор (машина, вид) — последняя
    many = [{'car': f'C{i}', 'key': 'stop', 'since': None} for i in range(47)] + [
        {'car': 'CAR9', 'key': 'stop', 'since': None}, {**item, 'since': None}, item]
    r = client.post(URL, json={'items': many}, base_url=LAN, headers=h)
    assert r.status_code == 200 and r.get_json()['acks'] == [_ack('CAR1', 'speed', SINCE, 'boss'),
                                                             _ack('CAR9', 'stop', None, 'boss')]
    r = client.post(URL, json={'items': [{**item, 'car': 'NOPE'}]}, base_url=LAN, headers=h)   # только чужие — ничего
    assert r.status_code == 200 and len(r.get_json()['acks']) == 2
    # GET: отметки дня — у флота; у карточки машины — нет (страница их не читает)
    body = client.get('/api/routes/live', base_url=LAN).get_json()
    assert body['acks'] == [_ack('CAR1', 'speed', SINCE, 'boss'), _ack('CAR9', 'stop', None, 'boss')]
    assert 'acks' not in client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()
    assert client.get('/api/routes/live?date=2026-10-02', base_url=LAN).get_json()['acks'] == []
    # «Гараж» (и из интернета) отмечает — с именем; новая отметка вида заменяет прежнюю
    hg = _session_as(client, 'garage1', base=LAN)
    r = client.post(URL, json={'items': [{**item, 'since': None, 'key': 'late:window'}]}, base_url=LAN, headers=hg)
    assert r.status_code == 200 and _ack('CAR1', 'late:window', None, 'garage1') in r.get_json()['acks']
    assert live_app._garage_path_allowed(URL, 'POST') is True and live_app._public_path_allowed(URL, 'POST') is True
    assert live_app._garage_path_allowed(URL + '/x', 'POST') is False and live_app._garage_path_allowed(URL, 'GET') is True
    hg = _session_as(client, 'garage1')
    r = client.post(URL, json={'items': [{**item, 'since': '2026-10-03T10:50:00+04:00'}]}, base_url=PUBLIC, headers=hg)
    assert r.status_code == 200
    (speed,) = [a for a in state.store.live_acks('2026-10-03') if a['car'] == 'CAR1' and a['key'] == 'speed']
    assert (speed['since'], speed['user']) == ('2026-10-03T10:50:00+04:00', 'garage1')
    # прочие роли — нет; «Բացատրել» гаражу по-прежнему закрыт
    hu = _session_as(client, 'u', base=LAN)
    assert client.post(URL, json={'items': [item]}, base_url=LAN, headers=hu).status_code == 403
    assert live_app._garage_path_allowed('/api/routes/live/explain', 'POST') is False


def test_api_ack_refresh_keeps_who_and_when(client, live_app, monkeypatch):
    """Ревью M1: подтверждение страницей (refresh — дребезг сдвинул since, отметка без since держится) не меняет, кто и
    когда нажал «Տեսա»: «Տեսավ՝» — по-прежнему тот, кто нажал."""
    import test_route_live as trl
    from route_optimizer import views
    h = _session_as(client, 'boss', base=LAN)
    assert client.post(URL, json={'items': [{'car': 'CAR1', 'key': 'late:window', 'since': None},
                                            {'car': 'CAR1', 'key': 'stop', 'since': SINCE}]},
                       base_url=LAN, headers=h).status_code == 200
    later = trl.API_NOW + timedelta(minutes=6)
    monkeypatch.setattr(views, '_yerevan_now', lambda: later)
    hg = _session_as(client, 'garage1', base=LAN)
    moved = '2026-10-03T10:42:00+04:00'
    r = client.post(URL, json={'refresh': True, 'items': [{'car': 'CAR1', 'key': 'late:window', 'since': None},
                                                          {'car': 'CAR1', 'key': 'stop', 'since': moved}]},
                    base_url=LAN, headers=hg)
    seen = later.isoformat(timespec='seconds')
    assert r.status_code == 200 and r.get_json()['acks'] == [_ack('CAR1', 'late:window', None, 'boss', seen_at=seen),
                                                             _ack('CAR1', 'stop', moved, 'boss', seen_at=seen)]
    # подтверждение без строки — как нажатие (своя отметка страницы, не дошедшая раньше)
    r = client.post(URL, json={'refresh': True, 'items': [{'car': 'CAR9', 'key': 'stop', 'since': None}]},
                    base_url=LAN, headers=hg)
    assert _ack('CAR9', 'stop', None, 'garage1', at=seen) in r.get_json()['acks']
    # нажатие — меняет, кто и когда
    r = client.post(URL, json={'items': [{'car': 'CAR1', 'key': 'stop', 'since': moved}]}, base_url=LAN, headers=hg)
    assert _ack('CAR1', 'stop', moved, 'garage1', at=seen) in r.get_json()['acks']


def test_live_map_survives_missing_ack_table(client, live_app, tmp_path, monkeypatch):
    """Ревью M3: база с номером схемы 28 без live_ack (или первой версии ветки без seen_at) — таблица досоздаётся при
    открытии; отметки не прочитались — карта отвечает без них, а не 500."""
    path = str(tmp_path / 'r.db')
    st.Store(path).load()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE live_ack')
        conn.commit()
    assert st.Store(path).live_acks('2026-10-03') == []
    with closing(sqlite3.connect(path)) as conn:   # первая версия ветки: без seen_at
        conn.execute('DROP TABLE live_ack')
        conn.execute("CREATE TABLE live_ack(day TEXT NOT NULL, car TEXT NOT NULL, key TEXT NOT NULL, since TEXT, "
                     "user TEXT, at TEXT NOT NULL, PRIMARY KEY (day, car, key))")
        conn.execute("INSERT INTO live_ack VALUES('2026-10-03', 'CAR1', 'gps', NULL, 'boss', '2026-10-03T10:00:00+04:00')")
        conn.commit()
    assert st.Store(path).live_acks('2026-10-03') == [_ack('CAR1', 'gps', None, 'boss', at='2026-10-03T10:00:00+04:00')]

    def broken(day):
        raise st.StoreError('нет базы')
    monkeypatch.setattr(live_app.app.extensions['route_optimizer'].store, 'live_acks', broken)
    _session_as(client, 'boss', base=LAN)
    r = client.get('/api/routes/live', base_url=LAN)
    assert r.status_code == 200 and r.get_json()['acks'] == [] and r.get_json()['trucks']


def test_api_ack_view_refuses_role_without_map(live_app):
    """Вторая линия: роль, которой гейт когда-нибудь откроет путь, отметку всё равно не запишет."""
    from flask import g
    from route_optimizer import views
    body = {'items': [{'car': 'CAR1', 'key': 'stop', 'since': None}]}
    with live_app.app.test_request_context(URL, method='POST', json=body):
        g.user_role = 'warehouse'
        _, code = views.api_live_ack()
        assert code == 403
    assert live_app.app.extensions['route_optimizer'].store.live_acks('2026-10-03') == []

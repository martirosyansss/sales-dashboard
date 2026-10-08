# -*- coding: utf-8 -*-
"""Тревоги карты машин, круг 2 (владелец 08.10 «fix all»): общая «Տեսա» на сервере (схема 27, store.live_acks,
POST /api/routes/live/ack, acks в ответах карты) и эскалация в Telegram красной тревоги, которую 10 минут никто не отметил
(live_alerts: одно сообщение на случай, не для жёлтых, тихие часы).

Базы — только временные копии: схема 26 — созданная кодом и откатанная, база владельца — копия через sqlite backup
(источник открыт только для чтения). Telegram не вызывается: отправка — подделка.
Запуск:  python -m pytest tests/test_route_live_ack.py -q
"""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import live, live_alerts as la  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_live import live_app  # noqa: E402,F401
from test_route_live_alerts import NOW, Sender, alert, at, card  # noqa: E402

OWNER_DB = Path('F:/New Softs/Sales Dashboard/route_optimizer.db')   # база ПК владельца — только чтение, только копия


def _version_tables(path):
    with closing(sqlite3.connect(path)) as conn:
        v = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    return v, counts


# ============================== база: схема 27 ==============================

def test_schema_27_migrates_copy_of_schema_26_and_acks_roundtrip(tmp_path):
    src = str(tmp_path / 'v26.db')
    s = st.Store(src)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    a, b = '2026-10-03T10:00:00+04:00', '2026-10-03T10:12:00+04:00'
    s.add_live_explanation('2026-10-03', 'CAR1', 'deviation', a, b, 'road', 'խցանում', 'boss')
    with closing(sqlite3.connect(src)) as conn:   # база схемы 26: та же, только без live_ack (шаг 26 → 27 — только добавляет)
        conn.execute('DROP TABLE live_ack')
        conn.execute("UPDATE meta SET value = '26' WHERE key = 'schema_version'")
        conn.commit()
    copy = str(tmp_path / 'copy.db')
    with closing(sqlite3.connect(f'file:{Path(src).as_posix()}?mode=ro', uri=True)) as x, \
            closing(sqlite3.connect(copy)) as y:
        x.backup(y)
    v0, before = _version_tables(copy)
    assert v0 == '26' and 'live_ack' not in before
    s2 = st.Store(copy)
    assert s2.live_acks('2026-10-03') == []
    v1, after = _version_tables(copy)
    assert v1 == str(st.SCHEMA_VERSION) == '27' and after == {**before, 'live_ack': 0}   # прежние строки целы
    assert st._MIGRATIONS[26] == (st._LIVE_ACK_TABLE,)
    assert s2.truck_drivers('2026-10-02')[0] == {'CAR1': 'Արամ'}
    assert s2.live_explanations('2026-10-03')['CAR1'][0]['note'] == 'խցանում'
    assert _version_tables(src)[0] == '26'   # источник не тронут
    # запись: строка на (день, машина, вид) — новая отметка заменяет случай, кто и когда
    s2.save_live_acks('2026-10-03', [('CAR1', 'speed', a), ('CAR1', 'late:window', None)], 'boss', a)
    s2.save_live_acks('2026-10-03', [('CAR1', 'speed', b)], 'garage1', b)
    assert s2.live_acks('2026-10-03') == [
        {'car': 'CAR1', 'key': 'late:window', 'since': None, 'user': 'boss', 'at': a},
        {'car': 'CAR1', 'key': 'speed', 'since': b, 'user': 'garage1', 'at': b}]
    assert s2.live_acks('2026-10-04') == []
    for bad in ([('', 'speed', None)], [('CAR1', 'Speed', None)], [('CAR1', 'speed', '10:00')], []):
        with pytest.raises(ValueError):
            s2.save_live_acks('2026-10-03', bad, 'boss', a)
    with pytest.raises(ValueError):
        s2.save_live_acks('03.10.2026', [('CAR1', 'speed', None)], 'boss', a)


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы ПК владельца')
def test_schema_27_migrates_copy_of_owner_db(tmp_path):
    """Копия настоящей базы владельца (sqlite backup, источник — только чтение): миграция до 27 не теряет ни строки."""
    copy = tmp_path / 'owner.db'
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro', uri=True)) as x, \
            closing(sqlite3.connect(copy)) as y:
        x.backup(y)
    v0, before = _version_tables(copy)
    if int(v0) > st.SCHEMA_VERSION:
        pytest.skip(f'база владельца новее кода (схема {v0})')
    s = st.Store(str(copy))
    s.load()
    v1, after = _version_tables(copy)
    assert v1 == str(st.SCHEMA_VERSION) and {k: after[k] for k in before} == before and after['live_ack'] >= 0
    s.save_live_acks('2026-10-08', [('333DN33', 'deviation', '2026-10-08T12:15:00+04:00')], 'qa', '2026-10-08T12:20:00+04:00')
    assert s.live_acks('2026-10-08')[-1]['user'] == 'qa'


# ============================== API «Տեսա» ==============================

URL = '/api/routes/live/ack'
SINCE = '2026-10-03T10:40:00+04:00'


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
                       ({'items': [item], 'date': '2026-10-02'}, 'date'), ({'items': [item], 'date': '03.10.2026'}, 'date')):
        r = client.post(URL, json=bad, base_url=LAN, headers=h)
        assert r.status_code == 400 and field in r.get_json()['errors'], bad
    assert state.store.live_acks('2026-10-03') == []
    # 50 — можно; повтор (машина, вид) в запросе — последняя
    many = [{'car': f'C{i}', 'key': 'stop', 'since': None} for i in range(49)] + [item, {**item, 'since': None}]
    assert client.post(URL, json={'items': many[1:]}, base_url=LAN, headers=h).status_code == 200
    r = client.post(URL, json={'date': '2026-10-03', 'items': [item]}, base_url=LAN, headers=h)
    assert r.status_code == 200
    mine = [a for a in r.get_json()['acks'] if a['car'] == 'CAR1']
    assert mine == [{'car': 'CAR1', 'key': 'speed', 'since': SINCE, 'user': 'boss', 'at': '2026-10-03T11:00:00+04:00'}]
    # GET: отметки дня — у флота все, у машины — её
    body = client.get('/api/routes/live', base_url=LAN).get_json()
    assert len(body['acks']) == 49 and {'car': 'CAR1', 'key': 'speed', 'since': SINCE, 'user': 'boss',
                                        'at': '2026-10-03T11:00:00+04:00'} in body['acks']
    assert client.get('/api/routes/live/truck?car=CAR1', base_url=LAN).get_json()['acks'] == mine
    assert client.get('/api/routes/live?date=2026-10-02', base_url=LAN).get_json()['acks'] == []
    # «Гараж» (и из интернета) отмечает — с именем; новая отметка вида заменяет прежнюю
    hg = _session_as(client, 'garage1', base=LAN)
    r = client.post(URL, json={'items': [{**item, 'since': None, 'key': 'late:window'}]}, base_url=LAN, headers=hg)
    assert r.status_code == 200 and {'car': 'CAR1', 'key': 'late:window', 'since': None, 'user': 'garage1',
                                     'at': '2026-10-03T11:00:00+04:00'} in r.get_json()['acks']
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


def test_api_ack_view_refuses_role_without_map(live_app):
    """Вторая линия: роль, которой гейт когда-нибудь откроет путь, отметку всё равно не запишет."""
    from flask import g
    from route_optimizer import views
    with live_app.app.test_request_context(URL, method='POST', json={'items': [{'car': 'C', 'key': 'stop', 'since': None}]}):
        g.user_role = 'warehouse'
        _, code = views.api_live_ack()
        assert code == 403
    assert live_app.app.extensions['route_optimizer'].store.live_acks('2026-10-03') == []


# ============================== Telegram: эскалация ==============================

def _alerter(tmp_path, cards, acks=None, rules=live.Rules(), now=NOW, name='esc.json'):
    sender = Sender()
    box = {'cards': cards, 'now': now, 'rules': rules, 'acks': [] if acks is None else acks}
    alerter = la.LiveAlerter(lambda: (box['rules'], box['now'], box['cards']), sender, str(tmp_path / name),
                             acks=lambda day: box['acks'])
    return alerter, sender, box


def test_escalation_once_after_10_min_unacked_persists_over_restart(tmp_path):
    a = alert('speed', 0, max_kmh=104, lat=40.19, lon=44.51)
    alerter, sender, box = _alerter(tmp_path, {'CAR1': card(a)})
    assert alerter.tick() == 1 and not sender.sent[0].startswith('⚠')        # начало тревоги — как раньше
    box['now'] = NOW + timedelta(minutes=9, seconds=59)                       # 9 мин 59 с — ещё нет
    assert alerter.tick() == 0
    box['now'] = NOW + timedelta(minutes=10)
    assert alerter.tick() == 1
    lines = sender.sent[1].splitlines()
    assert lines[0] == '⚠ Չի տեսել ոչ ոք 10 րոպե՝ Արագության գերազանցում' and 'Մեքենա՝ CAR1 · JAC' in lines
    assert any(x.startswith('Որտեղ՝ https://yandex.ru/maps/') for x in lines)
    box['now'] = NOW + timedelta(minutes=30)
    assert alerter.tick() == 0                                                 # одно сообщение на случай
    again, sender2, box2 = _alerter(tmp_path, {'CAR1': card(a)}, now=NOW + timedelta(minutes=31))
    assert again.tick() == 0 and sender2.sent == []                           # перезапуск не повторяет
    # новый случай (другое начало, 11:20; поток увидел в 11:31) — своё начало и своя эскалация через 10 мин от первого
    # взгляда (окно повтора начала её не касается); в тексте — сколько тревога идёт
    box2['cards'] = {'CAR1': card(alert('speed', -20, max_kmh=110, lat=40.19, lon=44.51))}
    assert again.tick() == 1 and not sender2.sent[0].startswith('⚠')
    box2['now'] = NOW + timedelta(minutes=41)
    assert again.tick() == 1 and sender2.sent[1].startswith('⚠ Չի տեսել ոչ ոք 21 րոպե՝ Արագության գերազանցում')


def test_escalation_no_flood_for_old_reds_on_first_enable(tmp_path):
    """Поток стартовал, когда красная идёт уже 90 мин: сразу эскалации нет, через 10 мин без «Տեսա» — одна."""
    a = alert('center', 90, lat=40.18, lon=44.51)
    alerter, sender, box = _alerter(tmp_path, {'CAR1': card(a), 'CAR2': card(alert('gps', 120, gps='off'), car='CAR2')})
    alerter.tick()
    assert not any(x.startswith('⚠') for x in sender.sent)                   # давние красные — не разом
    box['now'] = NOW + timedelta(minutes=9)
    alerter.tick()
    assert not any(x.startswith('⚠') for x in sender.sent)
    box['acks'] = [{'car': 'CAR2', 'key': 'gps', 'since': at(120), 'user': 'boss', 'at': at(-9)}]   # GPS увидели
    box['now'] = NOW + timedelta(minutes=10)
    alerter.tick()
    esc = [x for x in sender.sent if x.startswith('⚠')]
    assert len(esc) == 1 and esc[0].startswith('⚠ Չի տեսել ոչ ոք 100 րոպե՝ Փոքր կենտրոնում')
    box['now'] = NOW + timedelta(minutes=40)
    alerter.tick()
    assert len([x for x in sender.sent if x.startswith('⚠')]) == 1


def test_escalation_not_when_acked_same_case(tmp_path):
    a = alert('gps', 15, gps='off')
    c = card(a)
    c['alerts'] = {'since': {'gps': a['from']}}
    ack = {'car': 'CAR1', 'key': 'gps', 'since': a['from'], 'user': 'boss', 'at': at(5)}
    alerter, sender, box = _alerter(tmp_path, {'CAR1': c}, acks=[ack])
    alerter.tick()
    assert [x for x in sender.sent if x.startswith('⚠')] == []
    box['acks'] = [{**ack, 'since': at(40)}]                                  # отметка прежнего случая — не в счёт
    box['now'] = NOW + timedelta(minutes=9)
    alerter.tick()
    assert [x for x in sender.sent if x.startswith('⚠')] == []                # 10 мин — от первого взгляда потока
    box['now'] = NOW + timedelta(minutes=10)
    alerter.tick()
    assert sender.sent[-1].startswith('⚠ Չի տեսել ոչ ոք 25 րոպե՝ GPS-')


def test_escalation_never_for_amber_and_only_enabled_kinds(tmp_path):
    cards = {'CAR1': card(alert('stop', 40, minutes=40, lat=40.1, lon=44.5), alert('deviation', 30, km=2.0, lat=40.1, lon=44.5),
                          alert('no_contact', 30, minutes=30))}
    alerter, sender, box = _alerter(tmp_path, cards)
    alerter.tick()
    box['now'] = NOW + timedelta(minutes=20)
    alerter.tick()
    assert sender.sent and not any(x.startswith('⚠') for x in sender.sent)
    # красная, вид выключен в настройках тревог — ни начала, ни эскалации
    rules = replace(live.Rules(), alert_kinds=('stop',))
    alerter, sender, _ = _alerter(tmp_path, {'CAR1': card(alert('center', 30, lat=40.18, lon=44.51))}, rules=rules,
                                  name='k.json')
    assert alerter.tick() == 0
    # объяснённая и «փոքր» — не эскалируются
    alerter, sender, _ = _alerter(tmp_path, {'CAR1': card(alert('sequence', 30, explained={'reason': 'road'}))},
                                  name='e.json')
    alerter.tick()
    assert sender.sent == []


def test_escalation_quiet_hours_and_unreadable_acks(tmp_path):
    rules = replace(live.Rules(), quiet=(10 * 60.0, 12 * 60.0))               # 10:00–12:00, сейчас 11:00
    a = alert('center', 15, lat=40.18, lon=44.51)
    alerter, sender, box = _alerter(tmp_path, {'CAR1': card(a)}, rules=rules)
    assert alerter.tick() == 0
    box['now'] = NOW + timedelta(minutes=10)                                  # срок — в тихие часы
    assert alerter.tick() == 0
    box['now'] = NOW + timedelta(hours=1, minutes=5)                          # тихие часы кончились — задним числом нет
    assert alerter.tick() == 0 and sender.sent == []
    # отметки не прочитаны (база недоступна) — эскалации нет, начало тревоги — как раньше
    sender = Sender()

    def broken(day):
        raise st.StoreError('нет базы')
    alerter = la.LiveAlerter(lambda: (live.Rules(), NOW, {'CAR2': card(alert('center', 30, lat=40.18, lon=44.51),
                                                                       car='CAR2')}),
                             sender, str(tmp_path / 'b.json'), acks=broken)
    assert alerter.tick() == 1 and not sender.sent[0].startswith('⚠')


def test_escalation_late_window_counts_from_first_seen_and_fresh_ack(tmp_path):
    late = {'late_kind': 'window', 'over_min': 25, 'name': 'Խանութ «Արարատ»', 'stop_id': 'S:1',
            'eta': at(-30), 'limit': at(-5)}
    c = {**card(), 'late': [late, {**late, 'late_kind': 'plan', 'name': 'Պլանային'}]}
    alerter, sender, box = _alerter(tmp_path, {'CAR1': c})
    assert alerter.tick() == 0                                                 # впервые увидена — отсчёт
    box['now'] = NOW + timedelta(minutes=9)
    assert alerter.tick() == 0
    # отметка без начала в силе 10 мин от подтверждения страницы
    box['acks'] = [{'car': 'CAR1', 'key': 'late:window', 'since': None, 'user': 'boss', 'at': at(-5)}]
    box['now'] = NOW + timedelta(minutes=12)
    assert alerter.tick() == 0
    box['now'] = NOW + timedelta(minutes=16)                                  # отметке 11 мин — страница её не держит
    assert alerter.tick() == 1
    text = sender.sent[0]
    assert text.startswith('⚠ Չի տեսել ոչ ոք 16 րոպե՝ Չի հասցնում ժամանակին') and 'Խանութ «Արարատ»' in text \
        and 'Պլանային' not in text
    # только «к плану» — жёлтая: нет; ушла до срока — отсчёт снимается
    alerter, sender, box = _alerter(tmp_path, {'CAR1': {**card(), 'late': [{**late, 'late_kind': 'plan'}]}}, name='p.json')
    alerter.tick()
    box['now'] = NOW + timedelta(minutes=30)
    assert alerter.tick() == 0
    alerter, sender, box = _alerter(tmp_path, {'CAR1': c}, name='g.json')
    alerter.tick()
    box['cards'] = {'CAR1': card()}
    box['now'] = NOW + timedelta(minutes=5)
    alerter.tick()
    box['cards'] = {'CAR1': c}
    box['now'] = NOW + timedelta(minutes=12)
    assert alerter.tick() == 0                                                 # вернулась — отсчёт заново
    box['now'] = NOW + timedelta(minutes=22)
    assert alerter.tick() == 1

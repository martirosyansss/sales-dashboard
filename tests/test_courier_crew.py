# -*- coding: utf-8 -*-
"""Второй человек в машине — «Առաքիչ» (docs/plans/crew-helper-plan.md, контракт v1.4 §8): courier.db 6 → 7,
POST/GET /crew (PIN помощника — тот же бюджет попыток и блокировка терминала, что /login), добавочные поля /login и
/status, helper_id событий (подтверждён / helper_unconfirmed / нет поля — старый APK), save_driver снимает помощника
(строка 'revoked': экипаж снова не решён, события после снятия — без него), офис «Առաքում այսօր» (экипаж машины,
помощник в таблицах, «план ≠ факт»).

Синтетические данные, без ERP; courier.db — только временная (копия базы владельца — только копия во временной папке,
оригинал открывается только на чтение).
Запуск из корня проекта:  python -m pytest tests/test_courier_crew.py -q
"""
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import courier  # noqa: E402
from courier import api as capi, clock, events as ev, store as cstore, views as cv  # noqa: E402
from courier.store import SCHEMA_VERSION, PinReset, Store  # noqa: E402
from route_optimizer.store import StoreError as RoutesStoreError  # noqa: E402
from test_courier import (NOW, FakeDb, _fresh_ref_cache, _pin_env, app, client, login, make_terminal, now,  # noqa: E402,F401
                          st)

DAY = NOW.date().isoformat()          # рабочий день решений экипажа (часы тестов — 2026-10-02 09:00 Ереван)
API = '/api/courier/v1'
SAME_PERSON = 'Սա վարորդի PIN-ն է։ Առաքիչը պետք է մուտքագրի իր PIN-ը։'
OWNER_COURIER = Path('F:/New Softs/Sales Dashboard/courier.db')   # живая база владельца: только чтение и копия


@pytest.fixture
def crew(st, client):
    """Терминал машины TEST: водитель Արամ (PIN 1234) вошёл; առաքիչ Բաբկեն (PIN 5678) заведён в офисе."""
    did, terminal, h = make_terminal(st)
    hid = st.store.save_driver(None, 'Բաբկեն', True, '5678', 'admin')
    s = login(client, h)
    return SimpleNamespace(driver_id=did, helper_id=hid, terminal=terminal, h=h, s=s)


def post_crew(client, s, body):
    return client.post(f'{API}/crew', json=body, headers=s)


def failed(st, terminal_id):
    return st.store.terminal(terminal_id).failed_pin_count


def crew_rows(st):
    with closing(sqlite3.connect(st.store.path)) as conn:
        return conn.execute('SELECT terminal_id, car_code, date, driver_id, helper_id, kind FROM crew_log ORDER BY id'
                            ).fetchall()


def session_helper(st):
    with closing(sqlite3.connect(st.store.path)) as conn:
        return [r[0] for r in conn.execute('SELECT helper_id FROM sessions')]


def day_event(etype='day_closed', day=DAY, helper=..., stop_id=None, payload=None, at='10:00:00'):
    e = {'id': str(uuid.uuid4()), 'type': etype, 'stop_id': stop_id, 'date': day, 'at': f'{day}T{at}+04:00',
         'payload': payload if payload is not None else {'summary': {}}}
    if helper is not ...:
        e['helper_id'] = helper
    return e


def stored_event(st, eid):
    with closing(sqlite3.connect(st.store.path)) as conn:
        r = conn.execute('SELECT driver_id, helper_id, flags FROM events WHERE id = ?', (eid,)).fetchone()
    return r[0], r[1], json.loads(r[2])


class FakeRoutesStore:
    """route_optimizer.Store для экипажа плана: truck_drivers (№62); load — «база битая» (routes_view → пустой вид)."""

    def __init__(self, drivers=None, helpers=None, fail=False):
        self.crew = {'driver': drivers or {}, 'helper': helpers or {}}
        self.fail = fail
        self.days = []
        self.calls = 0

    def truck_drivers(self, day, role='driver'):
        self.calls += 1
        if self.fail:
            raise RoutesStoreError('битая база')
        self.days.append(day)
        return dict(self.crew[role]), frozenset()

    def load(self):
        raise RoutesStoreError('нет')


def with_routes(app, **kw):
    """Подставить «Маршруты»; кэш плана терминала (api._planned) сбрасывается — новый план виден сразу."""
    fake = FakeRoutesStore(**kw)
    app.extensions['route_optimizer'] = SimpleNamespace(store=fake)
    app.extensions['courier'].crew_plan.clear()
    return fake


# ============================== миграция 6 → 7 ==============================

def _schema_shape(path):
    """Столбцы (имя, тип) экипажных таблиц и имена индексов — сравнение новой базы с мигрированной."""
    with closing(sqlite3.connect(path)) as conn:
        cols = {t: [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info({t})')]
                for t in ('sessions', 'events', 'crew_log')}
        idx = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"))
    return cols, idx


def _downgrade_to_v6(path):
    """База схемы 6, как её создаёт код 94e7994: без helper_id в sessions и events, без crew_log."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP INDEX events_helper')
        conn.execute('DROP TABLE crew_log')
        conn.execute('ALTER TABLE events DROP COLUMN helper_id')
        conn.execute('ALTER TABLE sessions DROP COLUMN helper_id')
        conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
        conn.commit()
        assert 'helper_id' not in {r[1] for r in conn.execute('PRAGMA table_info(events)')}


def test_migration_v6_to_v7_with_data(st, client, tmp_path):
    """Схема 6 с водителями, сессией и событиями → 7 одной транзакцией: всё на месте, сессия действует (вход не нужен),
    новые столбцы и crew_log — как у новой базы из _SCHEMA."""
    did, terminal, h = make_terminal(st)
    s = login(client, h)
    e = day_event(helper=...)
    assert ev.ingest(st.store, ev.Who(terminal.id, 'TEST', did, 'Արամ'), [e]).json()['accepted'] == [e['id']]
    _downgrade_to_v6(st.store.path)
    st.store = Store(st.store.path)
    assert [x['id'] for x in st.store.events_for_day(DAY)] == [e['id']]       # событие цело, помощника нет
    assert st.store.events_for_day(DAY)[0]['helper_id'] is None
    r = client.get(f'{API}/crew', headers=s)                                 # прежняя сессия действует
    assert r.status_code == 200 and r.get_json()['helper'] is None and r.get_json()['decided'] is False
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION) == '7'
        assert conn.execute('SELECT driver_id, helper_id FROM sessions').fetchall() == [(did, None)]
    fresh = Store(str(tmp_path / 'fresh.db'))
    fresh.list_drivers()
    assert _schema_shape(st.store.path) == _schema_shape(fresh.path)        # миграция = новая база
    assert post_crew(client, s, {'alone': True}).status_code == 200          # и работает


def test_migration_v6_to_v7_is_one_transaction(st, monkeypatch):
    """Сбой посреди миграции оставляет схему 6 целиком (ни столбцов, ни crew_log); следующий запуск мигрирует."""
    make_terminal(st)
    _downgrade_to_v6(st.store.path)

    def boom(conn):
        raise sqlite3.OperationalError('сбой посреди миграции')
    steps = cstore._MIGRATIONS[6]
    monkeypatch.setitem(cstore._MIGRATIONS, 6, (*steps[:2], boom))
    with pytest.raises(cstore.StoreError):
        Store(st.store.path).list_drivers()
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '6'
        assert 'helper_id' not in {r[1] for r in conn.execute('PRAGMA table_info(sessions)')}
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'crew_log'").fetchone() is None
    monkeypatch.setitem(cstore._MIGRATIONS, 6, steps)
    assert [d.name for d in Store(st.store.path).list_drivers()] == ['Արամ']


# ============================== POST/GET /crew ==============================

def test_crew_helper_confirmed_driver_session_intact(st, client, crew):
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'driver': {'id': crew.driver_id, 'name': 'Արամ'}, 'helper': {'id': crew.helper_id, 'name': 'Բաբկեն'},
                    'decided': True, 'revoked': None, 'planned': None}   # «Маршрутов» в приложении нет — null
    assert client.get(f'{API}/crew', headers=crew.s).get_json() == body
    # сессия водителя не менялась: тот же токен, тот же водитель, деньги — его
    assert client.get(f'{API}/status', headers=crew.s).status_code == 200
    assert client.get(f'{API}/day?date=2000-01-01', headers=crew.s).status_code == 200
    assert session_helper(st) == [crew.helper_id]
    assert crew_rows(st) == [(crew.terminal.id, 'TEST', DAY, crew.driver_id, crew.helper_id, 'helper')]
    assert failed(st, crew.terminal.id) == 0                                 # верный PIN — не попытка


def test_crew_alone_and_change(st, client, crew, now):
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['decided'] is False   # сразу после входа — не решён
    r = post_crew(client, crew.s, {'alone': True}).get_json()
    assert r['helper'] is None and r['decided'] is True
    assert post_crew(client, crew.s, {'helper_pin': '5678'}).get_json()['helper']['id'] == crew.helper_id
    assert post_crew(client, crew.s, {'alone': True}).get_json()['helper'] is None    # «Մնում եմ մենակ»
    assert [r[-1] for r in crew_rows(st)] == ['alone', 'helper', 'alone']
    assert session_helper(st) == [None]
    # новый вход позже — новая сессия: экипаж снова не решён, помощника нет (решение той же секунды, что вход, —
    # решение этой сессии: моменты сервера — до секунды)
    now['t'] = NOW + timedelta(minutes=30)
    s2 = login(client, crew.h)
    assert client.get(f'{API}/crew', headers=s2).get_json() == {
        'driver': {'id': crew.driver_id, 'name': 'Արամ'}, 'helper': None, 'decided': False, 'revoked': None,
        'planned': None}


@pytest.mark.parametrize('body', [{}, {'alone': True, 'helper_pin': '5678'}, {'alone': False}, {'alone': 1},
                                  {'alone': False, 'helper_pin': '5678'}, {'helper_pin': '12'}, {'helper_pin': 5678},
                                  {'helper_pin': None}])
def test_crew_bad_body_400_not_an_attempt(st, client, crew, body):
    r = post_crew(client, crew.s, body)
    assert r.status_code == 400 and r.get_json()['error'] == 'bad_request'
    assert failed(st, crew.terminal.id) == 0 and crew_rows(st) == []


def test_crew_requires_session(client, crew):
    r = client.post(f'{API}/crew', json={'alone': True}, headers=crew.h)
    assert r.status_code == 401 and r.get_json()['error'] == 'session'
    assert client.get(f'{API}/crew', headers=crew.h).status_code == 401


def test_crew_wrong_pin_shares_login_budget_and_lockout(st, client, crew, now):
    """Неверный PIN помощника — попытка терминала, как неверный вход: 2 на входе + 2 на /crew + 5-я → 429 на 15 мин;
    во время блокировки и верный PIN помощника, и вход — 429; сессия водителя при этом действует."""
    for _ in range(2):
        assert client.post(f'{API}/login', json={'pin': '9999'}, headers=crew.h).status_code == 403
    for _ in range(2):
        r = post_crew(client, crew.s, {'helper_pin': '9999'})
        assert r.status_code == 403 and r.get_json()['error'] == 'pin'
    assert failed(st, crew.terminal.id) == 4
    r = post_crew(client, crew.s, {'helper_pin': '9999'})
    assert r.status_code == 429 and r.get_json()['error'] == 'locked' and r.get_json()['retry_after'] == 900
    now['t'] = NOW + timedelta(minutes=5)
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 429 and r.get_json()['error'] == 'locked'
    assert client.post(f'{API}/login', json={'pin': '1234'}, headers=crew.h).status_code == 429
    assert client.get(f'{API}/status', headers=crew.s).status_code == 200   # водитель работает дальше
    assert crew_rows(st) == [] and session_helper(st) == [None]
    now['t'] = NOW + timedelta(minutes=16)
    assert post_crew(client, crew.s, {'helper_pin': '5678'}).status_code == 200


def test_crew_same_person_409(st, client, crew):
    r = post_crew(client, crew.s, {'helper_pin': '1234'})
    assert r.status_code == 409 and r.get_json() == {'error': 'same_person', 'message': SAME_PERSON}
    assert failed(st, crew.terminal.id) == 0                                 # свой PIN верный — не попытка


def test_crew_same_person_keeps_attempt_window(st, client, crew):
    """409 same_person не сбрасывает и не двигает счётчик неверных PIN терминала (и его окно)."""
    for _ in range(2):
        assert client.post(f'{API}/login', json={'pin': '9999'}, headers=crew.h).status_code == 403
    with closing(sqlite3.connect(st.store.path)) as conn:
        before = conn.execute('SELECT failed_pin_count, pin_window_start, locked_until FROM terminals').fetchone()
    assert post_crew(client, crew.s, {'helper_pin': '1234'}).status_code == 409
    with closing(sqlite3.connect(st.store.path)) as conn:
        assert conn.execute('SELECT failed_pin_count, pin_window_start, locked_until FROM terminals').fetchone() == before
    assert before[0] == 2
    assert crew_rows(st) == [] and session_helper(st) == [None]
    assert client.get(f'{API}/status', headers=crew.s).status_code == 200


def test_crew_inactive_helper_403_counts(st, client, crew):
    st.store.save_driver(crew.helper_id, 'Բաբկեն', False, None, 'admin')
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 403 and r.get_json()['error'] == 'pin'
    assert failed(st, crew.terminal.id) == 1 and crew_rows(st) == []


def test_crew_helper_pin_changed_during_check_403(st, client, crew, monkeypatch, now):
    """Гонка: PIN узнан, а офис за это время сменил помощнику PIN (updated_at другой) — 403 pin, решение не записано."""
    real = st.store.match_pin

    def match_then_change(pin):
        out = real(pin)
        now['t'] = NOW + timedelta(minutes=1)
        st.store.save_driver(crew.helper_id, 'Բաբկեն', True, '8765', 'admin')
        return out
    monkeypatch.setattr(st.store, 'match_pin', match_then_change)
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 403 and r.get_json()['error'] == 'pin'
    assert crew_rows(st) == [] and session_helper(st) == [None]


def test_crew_helper_deactivated_during_check_403(st, client, crew, monkeypatch):
    """Гонка: PIN узнан, а помощника выключили до записи решения — set_crew не пишет, 403 pin; сессия цела."""
    real = st.store.match_pin

    def match_then_deactivate(pin):
        out = real(pin)
        st.store.save_driver(crew.helper_id, 'Բաբկեն', False, None, 'admin')
        return out
    monkeypatch.setattr(st.store, 'match_pin', match_then_deactivate)
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 403 and r.get_json()['error'] == 'pin'
    assert crew_rows(st) == [] and session_helper(st) == [None]


def test_crew_pin_reset_403(st, client, crew, monkeypatch):
    def lost(pin):
        raise PinReset('нет перца')
    monkeypatch.setattr(st.store, 'match_pin', lost)
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 403 and r.get_json() == {'error': 'pin', 'message': capi.MSG['pin_reset']}
    assert failed(st, crew.terminal.id) == 1


def test_crew_duplicate_pin_403(st, client, crew, monkeypatch):
    two = [st.store.driver(crew.helper_id), st.store.driver(crew.driver_id)]
    monkeypatch.setattr(st.store, 'match_pin', lambda pin: two)
    r = post_crew(client, crew.s, {'helper_pin': '5678'})
    assert r.status_code == 403 and 'կրկնվում' in r.get_json()['message'] and crew_rows(st) == []


def test_crew_check_in_progress_429(client, crew):
    lock = capi._login_lock(crew.terminal.id)          # вход или PIN помощника этого терминала уже проверяется
    assert lock.acquire(blocking=False)
    try:
        r = post_crew(client, crew.s, {'helper_pin': '5678'})
        assert r.status_code == 429 and r.get_json()['retry_after'] == capi.LOGIN_BUSY_RETRY
    finally:
        lock.release()


# ============================== /login и /status: только добавочные поля ==============================

def test_login_and_status_additive_fields(app, st, client, crew):
    r = client.post(f'{API}/login', json={'pin': '1234'}, headers=crew.h).get_json()
    assert set(r) == {'session', 'expires_at', 'driver', 'car', 'helper', 'planned'}
    assert r['helper'] is None and r['planned'] is None
    s = {**crew.h, 'X-Courier-Session': r['session']}
    status = client.get(f'{API}/status', headers=s).get_json()
    assert set(status) == {'date', 'cash', 'rejected_events', 'crew'}
    assert status['crew'] == {'driver': {'id': crew.driver_id, 'name': 'Արամ'}, 'helper': None, 'decided': False,
                              'revoked': None, 'planned': None}
    post_crew(client, s, {'helper_pin': '5678'})
    assert client.get(f'{API}/status', headers=s).get_json()['crew']['helper'] == {'id': crew.helper_id, 'name': 'Բաբկեն'}


def test_planned_from_dispatch_or_null(app, st, client, crew):
    fake = with_routes(app, drivers={'TEST': 'Արամ'}, helpers={'TEST': 'Գուրգեն', 'OTHER': 'X'})
    r = client.post(f'{API}/login', json={'pin': '1234'}, headers=crew.h).get_json()
    assert r['planned'] == {'driver': 'Արամ', 'helper': 'Գուրգեն'} and fake.days[-1] == DAY
    s = {**crew.h, 'X-Courier-Session': r['session']}
    assert client.get(f'{API}/crew', headers=s).get_json()['planned'] == {'driver': 'Արամ', 'helper': 'Գուրգեն'}
    with_routes(app, helpers={'OTHER': 'X'})                                 # машины нет в плане
    assert client.get(f'{API}/crew', headers=s).get_json()['planned'] == {'driver': None, 'helper': None}
    with_routes(app, fail=True)                                              # база «Маршрутов» битая — null, не 500
    r = client.get(f'{API}/status', headers=s)
    assert r.status_code == 200 and r.get_json()['crew']['planned'] is None
    assert post_crew(client, s, {'alone': True}).get_json()['planned'] is None


def test_planned_cached_per_day(app, st, client, crew, monkeypatch):
    """План дня читается из «Маршрутов» не чаще раза в PLAN_TTL; сбой помнится только PLAN_FAIL_TTL."""
    tick = {'t': 1000.0}
    monkeypatch.setattr(capi.time, 'monotonic', lambda: tick['t'])
    fake = with_routes(app, helpers={'TEST': 'Գուրգեն'})
    for _ in range(3):
        assert client.get(f'{API}/status', headers=crew.s).get_json()['crew']['planned']['helper'] == 'Գուրգեն'
    assert fake.calls == 2                                                   # одно чтение: водители + առաքիչ
    fake.crew['helper']['TEST'] = 'Մուշեղ'                                   # логист сменил — видно после TTL
    tick['t'] += capi.PLAN_TTL - 1
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['planned']['helper'] == 'Գուրգեն'
    tick['t'] += 2
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['planned']['helper'] == 'Մուշեղ' and fake.calls == 4
    broken = with_routes(app, fail=True)
    for _ in range(3):
        assert client.get(f'{API}/crew', headers=crew.s).get_json()['planned'] is None
    assert broken.calls == 1
    broken.fail = False
    tick['t'] += capi.PLAN_FAIL_TTL
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['planned'] == {'driver': None, 'helper': None}


# ============================== события: helper_id ==============================

def _post(client, s, *events):
    r = client.post(f'{API}/events', json={'events': list(events)}, headers=s)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_event_helper_confirmed_unconfirmed_and_absent(st, client, crew):
    before = day_event(helper=crew.helper_id)                                # до подтверждения PIN — не подтверждён
    absent, null = day_event(), day_event(helper=None)                       # старый APK / один
    assert _post(client, crew.s, before, absent, null)['accepted'] == [before['id'], absent['id'], null['id']]
    assert stored_event(st, before['id']) == (crew.driver_id, None, ['helper_unconfirmed'])
    assert stored_event(st, absent['id']) == (crew.driver_id, None, [])
    assert stored_event(st, null['id']) == (crew.driver_id, None, [])
    post_crew(client, crew.s, {'helper_pin': '5678'})
    ok = day_event(helper=crew.helper_id)
    other = st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin')
    bad = [day_event(helper=other), day_event(helper=crew.driver_id), day_event(helper=str(crew.helper_id)),
           day_event(helper=True), day_event(helper=1.5), day_event(helper=2 ** 64), day_event(helper=-1),
           day_event(helper=0)]
    r = _post(client, crew.s, ok, *bad)
    assert r['accepted'] == [ok['id'], *[b['id'] for b in bad]] and not r['rejected']   # никогда не отказ
    assert stored_event(st, ok['id']) == (crew.driver_id, crew.helper_id, [])           # водитель — сессии
    for b in bad:
        assert stored_event(st, b['id']) == (crew.driver_id, None, ['helper_unconfirmed'])
    # офлайн-очередь: помощника уже сняли («մենակ»), событие сделано при нём — принимается
    post_crew(client, crew.s, {'alone': True})
    late = day_event(helper=crew.helper_id)
    _post(client, crew.s, late)
    assert stored_event(st, late['id'])[1] == crew.helper_id


@pytest.mark.parametrize('shift, confirmed', [(0, True), (1, True), (-1, True), (2, False), (-2, False)])
def test_event_helper_date_rule(st, client, crew, shift, confirmed):
    """Решение экипажа дня D подтверждает события дат D−1…D+1 (сессия идёт за полночь до 04:00), не D±2."""
    post_crew(client, crew.s, {'helper_pin': '5678'})
    e = day_event(day=(NOW.date() + timedelta(days=shift)).isoformat(), helper=crew.helper_id)
    _post(client, crew.s, e)
    assert stored_event(st, e['id'])[1:] == ((crew.helper_id, []) if confirmed else (None, ['helper_unconfirmed']))


def test_event_helper_other_terminal_or_driver_unconfirmed(st, client, crew):
    """Подтверждение действует только на своём терминале и у своего водителя."""
    post_crew(client, crew.s, {'helper_pin': '5678'})
    t2, _ = st.store.create_terminal('Urovo 2', 'TEST', 'admin')
    other_terminal = ev.ingest(st.store, ev.Who(t2.id, 'TEST', crew.driver_id, 'Արամ'), [day_event(helper=crew.helper_id)])
    d2 = st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin')
    other_driver = ev.ingest(st.store, ev.Who(crew.terminal.id, 'TEST', d2, 'Դավիթ'), [day_event(helper=crew.helper_id)])
    for res in (other_terminal, other_driver):
        assert stored_event(st, res.accepted[0])[1:] == (None, ['helper_unconfirmed'])


def test_money_stays_with_driver(st, client, crew):
    """Оплата при помощнике — деньги водителя сессии; у помощника (вошёл бы сам) — ничего."""
    post_crew(client, crew.s, {'helper_pin': '5678'})
    stop = client.get(f'{API}/day?date=2000-01-01', headers=crew.s).get_json()['stops'][0]
    pay = {'id': str(uuid.uuid4()), 'type': 'payment', 'stop_id': stop['stop_id'], 'date': '2000-01-01',
           'at': '2000-01-01T10:00:00+04:00', 'payload': {'amount': 500.0, 'kind': 'debt'}, 'helper_id': crew.helper_id}
    assert _post(client, crew.s, pay)['accepted'] == [pay['id']]
    assert client.get(f'{API}/status?date=2000-01-01', headers=crew.s).get_json()['cash']['collected_debt'] == 500.0
    hs = login(client, crew.h, pin='5678')
    assert client.get(f'{API}/status?date=2000-01-01', headers=hs).get_json()['cash']['collected_debt'] == 0.0


# ============================== save_driver снимает помощника ==============================

@pytest.mark.parametrize('change', ['deactivate', 'pin'])
def test_save_driver_clears_helper_from_sessions(st, client, crew, change):
    post_crew(client, crew.s, {'helper_pin': '5678'})
    if change == 'deactivate':
        st.store.save_driver(crew.helper_id, 'Բաբկեն', False, None, 'admin')
    else:
        st.store.save_driver(crew.helper_id, 'Բաբկեն', True, '8765', 'admin')
    assert session_helper(st) == [None]
    r = client.get(f'{API}/crew', headers=crew.s)                            # сессия водителя цела, экипаж — заново
    assert r.status_code == 200 and r.get_json()['helper'] is None and r.get_json()['decided'] is False
    # явный сигнал терминалу: кого и когда снял офис (момент — UTC, ключ clock.utc_key)
    revoked = {'id': crew.helper_id, 'name': 'Բաբկեն', 'at': clock.utc_key(NOW)}
    assert r.get_json()['revoked'] == revoked
    assert client.get(f'{API}/status', headers=crew.s).get_json()['crew'] == {
        'driver': {'id': crew.driver_id, 'name': 'Արամ'}, 'helper': None, 'decided': False, 'revoked': revoked,
        'planned': None}
    assert crew_rows(st)[-1] == (crew.terminal.id, 'TEST', DAY, crew.driver_id, crew.helper_id, 'revoked')


def test_revoke_events_after_flagged_before_kept_and_reconfirm(st, client, crew, now):
    """Офис сменил помощнику PIN в 09:00: событие 08:30 (сделано при нём, пришло позже) — с ним; 09:30 — без него и
    helper_unconfirmed; помощник подтвердился новым PIN в 09:10 — событие 09:40 снова с ним, экипаж решён."""
    now['t'] = NOW - timedelta(hours=1)                                      # 08:00 — подтвердился
    post_crew(client, crew.s, {'helper_pin': '5678'})
    now['t'] = NOW                                                           # 09:00 — офис сменил PIN
    st.store.save_driver(crew.helper_id, 'Բաբկեն', True, '8765', 'admin')
    before, after = day_event(helper=crew.helper_id, at='08:30:00'), day_event(helper=crew.helper_id, at='09:30:00')
    at_revoke = day_event(helper=crew.helper_id, at='09:00:00')
    assert len(_post(client, crew.s, before, after, at_revoke)['accepted']) == 3
    assert stored_event(st, before['id'])[1:] == (crew.helper_id, [])
    assert stored_event(st, after['id'])[1:] == (None, ['helper_unconfirmed'])
    assert stored_event(st, at_revoke['id'])[1:] == (None, ['helper_unconfirmed'])
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['revoked']['id'] == crew.helper_id
    now['t'] = NOW + timedelta(minutes=10)
    between = day_event(helper=crew.helper_id, at='09:05:00')                # между снятием и новым подтверждением
    r = post_crew(client, crew.s, {'helper_pin': '8765'}).get_json()
    assert r['decided'] is True and r['revoked'] is None and r['helper']['id'] == crew.helper_id
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['revoked'] is None
    again = day_event(helper=crew.helper_id, at='09:40:00')
    _post(client, crew.s, again, between)                                    # оба пришли после подтверждения
    assert stored_event(st, again['id'])[1:] == (crew.helper_id, [])
    assert stored_event(st, between['id'])[1:] == (None, ['helper_unconfirmed'])
    assert [r[-1] for r in crew_rows(st)] == ['helper', 'revoked', 'helper']


def test_revoke_only_sessions_with_that_helper(st, client, crew):
    """'revoked' — только сессиям, где он помощник; другой терминал и чужой помощник не трогаются."""
    _, t2, h2 = make_terminal(st, car='CAR2', pin='2222', name='Վահե')
    s2 = login(client, h2, pin='2222')
    other = st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin')
    post_crew(client, crew.s, {'helper_pin': '5678'})
    post_crew(client, s2, {'helper_pin': '4321'})
    st.store.save_driver(crew.helper_id, 'Բաբկեն', False, None, 'admin')
    assert [r for r in crew_rows(st) if r[-1] == 'revoked'] == [
        (crew.terminal.id, 'TEST', DAY, crew.driver_id, crew.helper_id, 'revoked')]
    assert client.get(f'{API}/crew', headers=s2).get_json()['helper']['id'] == other
    assert client.get(f'{API}/crew', headers=s2).get_json()['decided'] is True


def test_reset_unverifiable_clears_helper_and_revokes(st, client, crew):
    """Офис явно сбросил PIN, который не проверить (перца нет в среде), — человек снят с сессий как помощник."""
    post_crew(client, crew.s, {'helper_pin': '5678'})
    with closing(sqlite3.connect(st.store.path)) as conn:                   # PIN помощника — с потерянным перцем
        conn.execute("UPDATE drivers SET pin_tag = ?, pin_scheme = 'pepper:deadbeef' WHERE id = ?",
                     ('p2:deadbeef:' + 'a' * 64, crew.helper_id))
        conn.commit()
    with pytest.raises(cstore.PinPepperMissing):
        st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin')
    assert session_helper(st) == [crew.helper_id]                           # без явного сброса — ничего
    st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin', reset_unverifiable=True)
    assert session_helper(st) == [None]
    assert crew_rows(st)[-1][-2:] == (crew.helper_id, 'revoked')
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['decided'] is False


def test_save_driver_rename_keeps_helper(st, client, crew):
    post_crew(client, crew.s, {'helper_pin': '5678'})
    st.store.save_driver(crew.helper_id, 'Բաբկեն Բ.', True, None, 'admin')
    assert session_helper(st) == [crew.helper_id]
    assert client.get(f'{API}/crew', headers=crew.s).get_json()['helper']['name'] == 'Բաբկեն Բ.'


# ============================== офис «Առաքում այսօր» ==============================

def _overview(app):
    with app.test_request_context():
        return cv.day_overview(NOW.date(), load=False)


def test_day_overview_crew_helpers_alone_and_tables(app, st, client, crew):
    post_crew(client, crew.s, {'helper_pin': '5678'})
    flagged = day_event('arrived', stop_id='S:' + str(uuid.uuid4()).upper(), helper=crew.helper_id,
                        payload={'lat': 40.18, 'lon': 44.51, 'accuracy': 10.0})   # unknown_stop — в «Ուշադրություն»
    _post(client, crew.s, flagged)
    # вторая машина: водитель один весь день; третья — старый APK (экипаж не сообщает)
    d2, t2, h2 = make_terminal(st, car='CAR2', pin='2222', name='Վահե')
    s2 = login(client, h2, pin='2222')
    post_crew(client, s2, {'alone': True})
    d3 = st.store.save_driver(None, 'Սուրեն', True, '3333', 'admin')
    t3, _ = st.store.create_terminal('Urovo 3', 'CAR3', 'admin')
    ev.ingest(st.store, ev.Who(t3.id, 'CAR3', d3, 'Սուրեն'), [day_event()])
    out = _overview(app)
    cars = {c['car_code']: c for c in out['cars']}
    assert (cars['TEST']['drivers'], cars['TEST']['helpers'], cars['TEST']['alone']) == (['Արամ'], ['Բաբկեն'], False)
    assert (cars['CAR2']['drivers'], cars['CAR2']['helpers'], cars['CAR2']['alone']) == (['Վահե'], [], True)
    assert (cars['CAR3']['drivers'], cars['CAR3']['helpers'], cars['CAR3']['alone']) == (['Սուրեն'], [], False)
    (row,) = [f for f in out['flagged'] if f['id'] == flagged['id']]
    assert (row['driver_name'], row['helper_name']) == ('Արամ', 'Բաբկեն')


def test_day_overview_helper_until_revoke(app, st, client, crew, now):
    """Офис снял помощника в 11:15 — на карточке «до 11:15»; подтвердился снова — без «до»."""
    post_crew(client, crew.s, {'helper_pin': '5678'})
    now['t'] = NOW + timedelta(hours=2, minutes=15)
    st.store.save_driver(crew.helper_id, 'Բաբկեն', True, '8765', 'admin')
    (car,) = _overview(app)['cars']
    assert car['helpers'] == ['Բաբկեն'] and car['helper_until'] == {'Բաբկեն': f'{DAY}T11:15:00+04:00'}
    assert car['alone'] is False
    now['t'] += timedelta(minutes=5)
    post_crew(client, crew.s, {'helper_pin': '8765'})
    assert _overview(app)['cars'][0]['helper_until'] == {}


def test_day_overview_helper_info_per_person_in_order(app, st, client, crew, now):
    """Каждый առաքիչ дня — отдельно, в порядке первого подтверждения: Կարեն с 09:00, снят в 10:15; Բաբկեն с 10:25."""
    karen = st.store.save_driver(None, 'Կարեն Ավետիսյան', True, '6666', 'admin')
    post_crew(client, crew.s, {'helper_pin': '6666'})                       # 09:00
    now['t'] = NOW + timedelta(hours=1, minutes=15)
    st.store.save_driver(karen, 'Կարեն Ավետիսյան', False, None, 'admin')     # 10:15 — офис выключил
    now['t'] = NOW + timedelta(hours=1, minutes=25)
    post_crew(client, crew.s, {'helper_pin': '5678'})                       # 10:25 — Բաբկեն
    (car,) = _overview(app)['cars']
    assert car['helpers'] == ['Բաբկեն', 'Կարեն Ավետիսյան']                  # алфавит — как раньше
    assert car['helper_info'] == [
        {'name': 'Կարեն Ավետիսյան', 'since': f'{DAY}T09:00:00+04:00', 'until': f'{DAY}T10:15:00+04:00'},
        {'name': 'Բաբկեն', 'since': f'{DAY}T10:25:00+04:00', 'until': None}]


def test_rejected_shows_confirmed_helper_only(app, st, client, crew):
    post_crew(client, crew.s, {'helper_pin': '5678'})
    other = st.store.save_driver(None, 'Դավիթ', True, '4321', 'admin')
    bad = [day_event('nope', helper=crew.helper_id), day_event('nope', helper=other), day_event('nope')]
    assert len(_post(client, crew.s, *bad)['rejected']) == 3
    with app.test_request_context():
        rej = {r['id']: r['helper_name'] for r in st.store.rejected_for_day(DAY)}
    assert rej == {bad[0]['id']: 'Բաբկեն', bad[1]['id']: None, bad[2]['id']: None}


def test_rejected_helper_respects_revoke(app, st, client, crew, now):
    """Отказ — по тому же правилу снятия, что принятое событие: момент — `at` тела (до снятия — с помощником, после —
    без); `at` не разобрать — момент получения отказа; между снятием и новым подтверждением — без помощника."""
    now['t'] = NOW - timedelta(hours=1)                                      # 08:00 подтвердился
    post_crew(client, crew.s, {'helper_pin': '5678'})
    now['t'] = NOW                                                           # 09:00 офис сменил PIN
    st.store.save_driver(crew.helper_id, 'Բաբկեն', True, '8765', 'admin')
    before = day_event('nope', helper=crew.helper_id, at='08:30:00')
    after = day_event('nope', helper=crew.helper_id, at='09:30:00')
    bad_at = {**day_event('nope', helper=crew.helper_id), 'at': 'вчера'}    # получен в 09:20 — после снятия
    now['t'] = NOW + timedelta(minutes=20)
    assert len(_post(client, crew.s, before, after, bad_at)['rejected']) == 3
    now['t'] = NOW + timedelta(minutes=40)                                   # 09:40 подтвердился заново
    post_crew(client, crew.s, {'helper_pin': '8765'})
    between = day_event('nope', helper=crew.helper_id, at='09:35:00')
    later = day_event('nope', helper=crew.helper_id, at='09:45:00')
    _post(client, crew.s, between, later)
    with app.test_request_context():
        rej = {r['id']: r['helper_name'] for r in st.store.rejected_for_day(DAY)}
    assert rej == {before['id']: 'Բաբկեն', after['id']: None, bad_at['id']: None, between['id']: None,
                   later['id']: 'Բաբկեն'}


def test_crew_plan_mismatch(app, st, client, crew):
    post_crew(client, crew.s, {'helper_pin': '5678'})
    _, _, h2 = make_terminal(st, car='CAR2', pin='2222', name='Վահե')
    post_crew(client, login(client, h2, pin='2222'), {'alone': True})
    # совпадение — без регистра и лишних пробелов; CAR2: առաքիչ по плану, а водитель один; CAR9 — фактов нет
    with_routes(app, drivers={'TEST': '  արամ ', 'CAR2': 'Կարեն', 'CAR9': 'Ն'},
                helpers={'TEST': 'ԲԱԲԿԵՆ', 'CAR2': 'Գուրգեն', 'CAR9': 'Մ'})
    body = client.get(f'/api/courier/admin/today?date={DAY}').get_json()
    assert body['success'] and body['crew_mismatch'] == {'available': True, 'items': [
        {'car_code': 'CAR2', 'role': 'driver', 'planned': 'Կարեն', 'fact': ['Վահե']},
        {'car_code': 'CAR2', 'role': 'helper', 'planned': 'Գուրգեն', 'fact': []},
    ], 'planned': {'CAR2': {'driver': 'Կարեն', 'helper': 'Գուրգեն'}, 'TEST': {'driver': '  արամ ', 'helper': 'ԲԱԲԿԵՆ'}}}
    with_routes(app, drivers={'TEST': 'Արամ'}, helpers={'TEST': 'Գուրգեն'})   # другой առաքիչ
    items = client.get(f'/api/courier/admin/today?date={DAY}').get_json()['crew_mismatch']['items']
    assert items == [{'car_code': 'TEST', 'role': 'helper', 'planned': 'Գուրգեն', 'fact': ['Բաբկեն']}]


def test_crew_plan_mismatch_without_routes(app, st, client, crew):
    post_crew(client, crew.s, {'alone': True})
    body = client.get(f'/api/courier/admin/today?date={DAY}').get_json()     # раздела «Маршруты» нет
    assert body['success'] and body['crew_mismatch'] == {'available': False, 'items': []}
    with_routes(app, fail=True)                                              # база «Маршрутов» битая
    r = client.get(f'/api/courier/admin/today?date={DAY}')
    assert r.status_code == 200 and r.get_json()['crew_mismatch'] == {'available': False, 'items': []}


def test_crew_name_normalization():
    assert cv._crew_name('  Արամ   Պետրոսյան ') == cv._crew_name('արամ պետրոսյան')
    assert cv._crew_name('Արամ') != cv._crew_name('Արամ Պ')


def test_office_page_hint_and_asset_versions(app, client):
    app.add_url_rule('/logout', 'logout', lambda: '')
    html = client.get('/courier').data.decode('utf-8')
    assert 'Առաքիչը' in html and 'իր PIN-ով' in html
    assert 'js/courier.js?v=15' in html and 'css/courier.css?v=10' in html


def test_today_old_events_unchanged_shape(app, st, client, crew):
    """Старый APK: событие без helper_id — в таблицах офиса helper_name None, машина без helpers и без alone."""
    e = day_event('arrived', stop_id='S:' + str(uuid.uuid4()).upper(), payload={'lat': 40.18, 'lon': 44.51})
    _post(client, crew.s, e)
    out = _overview(app)
    (car,) = out['cars']
    assert car['helpers'] == [] and car['alone'] is False and car['drivers'] == ['Արամ']
    assert [f['helper_name'] for f in out['flagged']] == [None]


@pytest.mark.skipif(not OWNER_COURIER.exists(), reason='нет courier.db владельца')
def test_courier_owner_copy_migrates_to_v7(app, st, client, tmp_path):
    """КОПИЯ courier.db владельца (схема 6): миграция только добавляет — строки всех таблиц как были; на копии сессия
    действует и /crew работает. Оригинал открывается только на чтение (резервная копия SQLite — согласованная и при
    работающем сервере), в него ничего не пишется."""
    copy = tmp_path / 'owner_courier.db'
    with closing(sqlite3.connect(f'file:{OWNER_COURIER.as_posix()}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(str(copy))) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(str(copy))) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name != 'sqlite_sequence'")]
        before = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    if version != '6':
        pytest.skip(f'courier.db владельца схемы {version}, не 6')
    st.store = Store(str(copy))
    st.store.list_terminals()
    with closing(sqlite3.connect(str(copy))) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '7'
        assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables} == before
        assert conn.execute('SELECT COUNT(*) FROM crew_log').fetchone() == (0,)
        assert conn.execute('SELECT COUNT(*) FROM events WHERE helper_id IS NOT NULL').fetchone() == (0,)
    # сессия на копии: новый терминал и человек без PIN (PIN владельца и перец не нужны) — /crew работает
    did = st.store.save_driver(None, 'Թեստ Կրկնօրինակ', True, None, 'test')
    terminal, token = st.store.create_terminal('Copy test', 'TEST', 'test')
    session = st.store.open_session(terminal.id, did, clock.session_expiry(clock.now()))
    s = {'Authorization': f'Bearer {token}', 'X-Courier-Session': session}
    assert client.get(f'{API}/crew', headers=s).get_json()['decided'] is False
    r = post_crew(client, s, {'alone': True})
    assert r.status_code == 200 and r.get_json()['decided'] is True and r.get_json()['helper'] is None

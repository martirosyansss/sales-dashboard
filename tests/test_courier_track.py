# -*- coding: utf-8 -*-
"""Контракт «Առաքիչ» v1.3 §7: приём трека машины (track) и заправок (refuel), хранение, миграция courier.db 5 → 6,
офис «Առաքում այսօր» (км по GPS, заправки).

Синтетические данные, без ERP; courier.db — только временная (копия базы владельца — только копия во временной папке).
Запуск из корня проекта:  python -m pytest tests/test_courier_track.py -q
"""
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import clock, events as ev  # noqa: E402
from courier.facts import FactsSource, delivered_share, gps_summary  # noqa: E402
from courier.store import SCHEMA_VERSION, TRACK_KEEP_DAYS, Store  # noqa: E402
from test_courier import (_fresh_ref_cache, _pin_env, _stop, app, client, login, make_terminal, now, st,  # noqa: E402,F401
                          term)

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
DAY = '2026-10-02'
SHOP = (40.18500, 44.50500)
OWNER_COURIER = ROOT / 'courier.db'   # база владельца рядом с app_v2.py: только читается и копируется во временную папку

_N = iter(range(1, 10 ** 6))


def _id():
    return '%08d-5555-4555-8555-555555555555' % next(_N)


def _pt(minute, lat=SHOP[0], lon=SHOP[1], acc=8.0, spd=5.0, brg=90.0, sec=0):
    return {'at': f'{DAY}T10:{minute:02d}:{sec:02d}+04:00', 'lat': lat, 'lon': lon, 'acc': acc, 'spd': spd, 'brg': brg}


def _track(points, eid=None, day=DAY, at=None):
    return {'id': eid or _id(), 'type': 'track', 'stop_id': None, 'date': day, 'at': at or f'{day}T10:59:00+04:00',
            'payload': {'points': points}}


def _refuel(liters=45.0, odo=123456, at='10:00', day=DAY, **kw):
    payload = {'liters': liters, 'odometer_km': odo, 'full_tank': True, 'amount_amd': 25000.0, 'lat': 40.18, 'lon': 44.51}
    payload.update(kw)
    return {'id': _id(), 'type': 'refuel', 'stop_id': None, 'date': day, 'at': f'{day}T{at}:00+04:00', 'payload': payload}


@pytest.fixture
def cs(tmp_path, monkeypatch):
    monkeypatch.setattr(clock, 'now', lambda: NOW)
    return Store(str(tmp_path / 'courier.db'))


def _who(store, car='CAR1'):
    did = store.save_driver(None, 'Արամ', True, str(1000 + len(store.list_drivers())), 'admin')   # PIN — у каждого свой
    terminal, _ = store.create_terminal('Urovo 1', car, 'admin')
    return ev.Who(terminal.id, car, did, 'Արամ')


def _points(store, car='CAR1'):
    with closing(sqlite3.connect(store.path)) as conn:
        return conn.execute('SELECT at_ms, date, lat, lon, acc, spd, brg FROM track_points WHERE car_code = ? '
                            'ORDER BY at_ms', (car,)).fetchall()


def _event_row(store, eid):
    with closing(sqlite3.connect(store.path)) as conn:
        r = conn.execute('SELECT type, stop_id, payload, flags FROM events WHERE id = ?', (eid,)).fetchone()
    return r and (r[0], r[1], json.loads(r[2]), json.loads(r[3]))


# ============================== track: проверка и хранение ==============================

def test_track_accepted_and_stored(cs):
    who = _who(cs)
    e = _track([_pt(1), _pt(2, spd=None, brg=None), _pt(3, acc=200.0, spd=60.0, brg=360.0)])
    assert ev.ingest(cs, who, [e]).json() == {'accepted': [e['id']], 'duplicates': [], 'rejected': []}
    rows = _points(cs)
    assert len(rows) == 3 and {r[1] for r in rows} == {DAY}
    first = datetime(2026, 10, 2, 10, 1, tzinfo=clock.YEREVAN)
    assert rows[0][0] == int(first.timestamp() * 1000) and rows[1][5:] == (None, None)
    # в events — только счётчики, точки не дублируются в JSON события
    assert _event_row(cs, e['id']) == ('track', None, {'points': 3, 'kept': 3, 'new': 3, 'dropped': {}}, [])


def test_track_bad_points_dropped_rest_kept(cs):
    who = _who(cs)
    pts = [_pt(1), _pt(2, acc=0), _pt(3, acc=200.5), _pt(4, acc=None), _pt(5, spd=60.1), _pt(6, spd=-1),
           _pt(7, brg=360.5), _pt(8, lat=55.75, lon=37.61), _pt(9, lat='40.18'), _pt(10, lat=True),
           {**_pt(11), 'at': '2026-10-02T10:11:00'},                        # без зоны
           'x', _pt(12), _pt(12), _pt(11, sec=30),                          # повтор того же at и не по возрастанию
           {**_pt(13), 'at': '2026-10-04T10:13:00+04:00'},                  # позже «сейчас + сутки»
           {**_pt(14), 'at': '2025-08-01T10:00:00+04:00'},                  # старше срока хранения
           _pt(15, acc=8)]
    r = ev.ingest(cs, who, [_track(pts)]).json()
    assert len(r['accepted']) == 1 and not r['rejected']
    payload = _event_row(cs, r['accepted'][0])[2]
    assert payload['points'] == len(pts) and payload['kept'] == payload['new'] == 3
    assert payload['dropped'] == {'acc': 3, 'spd': 2, 'brg': 1, 'place': 3, 'at': 1, 'bad': 1, 'duplicate': 1,
                                  'order': 1, 'future': 1, 'old': 1}
    assert [r[0] for r in _points(cs)] == sorted(r[0] for r in _points(cs)) and len(_points(cs)) == 3


@pytest.mark.parametrize('payload, message', [
    ({'points': None}, 'points'), ({}, 'points'), ({'points': 'x'}, 'points'),
    ({'points': [_pt(i % 60, sec=i // 60) for i in range(101)]}, 'points'),
    ({'points': [_pt(1, acc=0), _pt(2, lat=1.0)]}, 'GPS'),
])
def test_track_rejected_whole(cs, payload, message):
    who = _who(cs)
    e = {**_track([]), 'payload': payload}
    r = ev.ingest(cs, who, [e]).json()
    assert r['accepted'] == [] and message in r['rejected'][0]['message']
    assert _points(cs) == []


def test_track_empty_points_heartbeat_and_device(cs):
    """№76 (APK 2.2.0): пустой points — «на связи» без GPS-фикса; device — состояние терминала: проверенные поля, лишние
    ключи не хранятся, неверное значение — null; без device (старый APK) — как раньше."""
    who = _who(cs)
    device = {'battery': 57.6, 'charging': True, 'gps': 'off', 'net': 'cell', 'app': '2.2.0', 'imei': '123'}
    e = {**_track([]), 'payload': {'points': [], 'device': device}}
    assert ev.ingest(cs, who, [e]).json()['accepted'] == [e['id']]
    assert _event_row(cs, e['id'])[2] == {'points': 0, 'kept': 0, 'new': 0, 'dropped': {},
                                         'device': {'battery': 58, 'charging': True, 'gps': 'off', 'net': 'cell',
                                                    'app': '2.2.0'}}
    bad = {'battery': 101, 'charging': 'yes', 'gps': 'ON', 'net': 5, 'app': '2.2.0; drop table'}
    e2 = {**_track([_pt(1)]), 'payload': {'points': [_pt(1)], 'device': bad}}
    assert ev.ingest(cs, who, [e2]).json()['accepted'] == [e2['id']]
    assert _event_row(cs, e2['id'])[2]['device'] == {'battery': None, 'charging': None, 'gps': None, 'net': None,
                                                     'app': None}
    for battery in (True, -1, float('nan'), '50'):
        assert ev.track_device({'battery': battery})['battery'] is None
    assert ev.track_device({'battery': 0, 'gps': 'no_permission', 'net': None})['battery'] == 0
    assert ev.track_device('x') is None and ev.track_device(None) is None
    for dev in ('x', {}, {'gps': 'ON', 'battery': 101}, None):   # device нет или ни одного верного поля — отказ
        e3 = {**_track([]), 'payload': {'points': [], 'device': dev}}
        r = ev.ingest(cs, who, [e3]).json()
        assert r['accepted'] == [] and 'GPS' in r['rejected'][0]['message'], dev
    e4 = {**_track([]), 'payload': {'points': []}}                          # старый APK: пустой список — отказ
    assert ev.ingest(cs, who, [e4]).json()['accepted'] == []
    # точки были, ни одной годной: без верного device — отказ, с device — принято (состояние терминала нужно)
    junk = [_pt(1, acc=0)]
    r = ev.ingest(cs, who, [{**_track(junk), 'payload': {'points': junk}}]).json()
    assert r['accepted'] == [] and 'GPS' in r['rejected'][0]['message']
    r = ev.ingest(cs, who, [{**_track(junk), 'payload': {'points': junk, 'device': {'gps': 'ON'}}}]).json()
    assert r['accepted'] == []
    e5 = {**_track(junk, at=f'{DAY}T11:30:00+04:00'), 'payload': {'points': junk, 'device': {'gps': 'on'}}}
    assert ev.ingest(cs, who, [e5]).json()['accepted'] == [e5['id']]
    assert _event_row(cs, e5['id'])[2]['dropped'] == {'acc': 1} and len(_points(cs)) == 1


def test_track_empty_heartbeat_dropped_only_when_state_unchanged(cs):
    """№76: пустой heartbeat не позже 20 с после предыдущего события track принят, но не пишется — только если состояние
    терминала то же (батарея не в счёт); смена gps (выключили GPS) пишется всегда."""
    who = _who(cs)
    on = {'battery': 50, 'charging': False, 'gps': 'on', 'net': 'cell', 'app': '2.2.0'}

    def beat(sec, device, points=()):
        return {**_track(list(points), at=f'{DAY}T10:00:{sec:02d}+04:00'),
                'payload': {'points': list(points), 'device': device}}
    withpts = beat(0, on, [_pt(1)])                                          # пачка точек
    off = beat(5, {**on, 'gps': 'off'})                                      # сразу после неё — GPS выключили
    same = beat(10, {**on, 'gps': 'off', 'battery': 49})                     # то же состояние, другой заряд
    changed = beat(12, {**on, 'gps': 'off', 'net': 'wifi'})                  # сменилась сеть
    later = beat(40, {**on, 'gps': 'off', 'net': 'wifi'})                    # позже 20 с — пишется
    r = ev.ingest(cs, who, [withpts, off, same, changed, later]).json()
    assert r['accepted'] == [e['id'] for e in (withpts, off, same, changed, later)] and r['rejected'] == []
    assert [_event_row(cs, e['id']) is not None for e in (withpts, off, same, changed, later)] == [
        True, True, False, True, True]
    assert _event_row(cs, off['id'])[2]['device']['gps'] == 'off'
    # повтор уже принятого id — дубликат; повтор отброшенного — снова принят без записи
    again = ev.ingest(cs, who, [off, same]).json()
    assert again['duplicates'] == [off['id']] and again['accepted'] == [same['id']]
    assert _event_row(cs, same['id']) is None


def test_track_stop_id_not_required_and_date_suspicious(cs):
    who = _who(cs)
    e = _track([_pt(1)], day='2026-09-20', at=f'{DAY}T10:59:00+04:00')        # дата дня ≠ дата момента at
    assert ev.ingest(cs, who, [e]).json()['accepted'] == [e['id']]
    assert _event_row(cs, e['id'])[3] == ['date_suspicious']


def test_track_idempotent_and_dedup_by_car_and_moment(cs):
    who, other = _who(cs), _who(cs, 'CAR2')
    e1 = _track([_pt(1), _pt(2), _pt(3)])
    assert ev.ingest(cs, who, [e1]).json()['accepted'] == [e1['id']]
    again = ev.ingest(cs, who, [e1, {**e1, 'id': e1['id'].upper()}]).json()
    assert again['duplicates'] == [e1['id'], e1['id'].upper()] and again['accepted'] == []
    e2 = _track([_pt(2), _pt(3), _pt(4)])                                    # другое событие, две точки уже есть
    assert ev.ingest(cs, who, [e2]).json()['accepted'] == [e2['id']]
    assert _event_row(cs, e2['id'])[2]['new'] == 1 and len(_points(cs)) == 4
    e3 = _track([_pt(2)])                                                     # тот же момент у другой машины — своя точка
    assert ev.ingest(cs, other, [e3]).json()['accepted'] == [e3['id']]
    assert len(_points(cs, 'CAR2')) == 1 and len(_points(cs)) == 4
    # та же точка в одной пачке дважды (разные события) — одна строка
    e4, e5 = _track([_pt(20)]), _track([_pt(20)])
    assert ev.ingest(cs, who, [e4, e5]).json()['accepted'] == [e4['id'], e5['id']]
    assert len(_points(cs)) == 5


def test_track_100_points_full_precision_fit(cs):
    """100 точек с полной точностью double (float→double у acc/spd/brg) и миллисекундами — ≈ 17 КБ: в обычные 20 000
    символов влезает почти впритык, поэтому у track свой предел MAX_TRACK_PAYLOAD_BYTES."""
    who = _who(cs)
    pts = [{'at': f'{DAY}T10:{i // 60:02d}:{i % 60:02d}.123+04:00', 'lat': 40.181234567891234 + i * 1e-5,
            'lon': 44.512345678912345, 'acc': 12.345678329467773, 'spd': 11.234567642211914, 'brg': 270.12345886230469}
           for i in range(100)]
    size = len(json.dumps({'points': pts}, ensure_ascii=False))
    assert ev.MAX_PAYLOAD_BYTES * 0.8 < size < ev.MAX_TRACK_PAYLOAD_BYTES / 2
    e = _track(pts)
    assert ev.ingest(cs, who, [e]).json()['accepted'] == [e['id']] and len(_points(cs)) == 100
    big = _track([{**p, 'note': 'x' * 300} for p in pts])                    # лишние поля сверх предела — отказ
    assert 'մեծ' in ev.ingest(cs, who, [big]).json()['rejected'][0]['message']


def test_track_http_batch_of_200_full_events_fits_body_limit(st, client, now):
    """Пачка из 200 событий track по 100 точек (полная точность) — в пределах тела запроса MAX_BODY_BYTES."""
    from courier import api
    _, _, h = make_terminal(st, car='CAR1')
    s = login(client, h)
    now['t'] = NOW
    events = [_track([{'at': f'{DAY}T{8 + k // 60:02d}:{k % 60:02d}:{i // 2:02d}.{500 * (i % 2):03d}+04:00',
                       'lat': 40.181234567891234, 'lon': 44.512345678912345 + i * 1e-5, 'acc': 12.345678329467773,
                       'spd': 11.234567642211914, 'brg': 270.12345886230469} for i in range(100)]) for k in range(200)]
    body = json.dumps({'events': events}, separators=(',', ':'))
    assert len(body) < api.MAX_BODY_BYTES
    r = client.post('/api/courier/v1/events', data=body, content_type='application/json', headers=s)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert len(r.get_json()['accepted']) == 200
    assert len(st.store.track('CAR1', DAY)) == 200 * 100


def test_track_retention_purge_once_a_day(cs, monkeypatch):
    who = _who(cs)
    old_day = (NOW.date() - timedelta(days=TRACK_KEEP_DAYS + 1)).isoformat()
    old_ms = int(datetime.fromisoformat(old_day + 'T10:00:00+04:00').timestamp() * 1000)
    keep_ms = int((NOW - timedelta(days=TRACK_KEEP_DAYS - 1)).timestamp() * 1000)
    with closing(sqlite3.connect(cs.path)) as conn:
        conn.executemany('INSERT INTO track_points VALUES(?, ?, ?, 40.18, 44.5, 5, NULL, NULL)',
                         [('CAR1', old_ms, old_day), ('CAR1', keep_ms, DAY), ('GONE', old_ms, old_day)])
        conn.execute("INSERT INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, "
                     "received_at, payload) VALUES('old', 1, 1, 'GONE', ?, NULL, 'track', 'x', 'x', 'x', '{}')", (old_day,))
        conn.commit()
    assert ev.ingest(cs, who, [_track([_pt(1)])]).json()['rejected'] == []
    with closing(sqlite3.connect(cs.path)) as conn:
        assert sorted(r[0] for r in conn.execute('SELECT at_ms FROM track_points')) == sorted(
            [keep_ms, int(datetime(2026, 10, 2, 10, 1, tzinfo=clock.YEREVAN).timestamp() * 1000)])
        assert conn.execute("SELECT COUNT(*) FROM events WHERE id = 'old'").fetchone() == (0,)
        assert conn.execute("SELECT value FROM meta WHERE key = 'track_purged_on'").fetchone() == (NOW.date().isoformat(),)
        conn.execute('INSERT INTO track_points VALUES(?, ?, ?, 40.18, 44.5, 5, NULL, NULL)', ('CAR1', old_ms, old_day))
        conn.commit()
    ev.ingest(cs, who, [_track([_pt(2)])])                                   # тот же день — второй раз не чистит
    with closing(sqlite3.connect(cs.path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM track_points WHERE at_ms = ?', (old_ms,)).fetchone() == (1,)


# ============================== refuel ==============================

def test_refuel_validation(cs):
    who = _who(cs)
    ok = [_refuel(), _refuel(liters=400, odo=0, at='11:00'), _refuel(amount_amd=None, lat=None, lon=None, at='12:00'),
          _refuel(odo=123456.0, at='13:00', full_tank=False)]
    bad = [_refuel(liters=0), _refuel(liters=400.5), _refuel(liters='45'), _refuel(odo=123.5), _refuel(odo=-1),
           _refuel(odo=2_000_001), _refuel(odo=None), _refuel(full_tank='yes'), _refuel(amount_amd=-1),
           _refuel(lat=None), _refuel(lat=91.0, lon=44.5), _refuel(supersedes=5)]
    r = ev.ingest(cs, who, ok + bad).json()
    assert r['accepted'] == [e['id'] for e in ok]
    assert [x['id'] for x in r['rejected']] == [e['id'] for e in bad]
    no_flag = {k: v for k, v in ok[0]['payload'].items()}
    assert _event_row(cs, ok[0]['id'])[:3] == ('refuel', None, no_flag)
    missing = _refuel(at='14:00', odo=123500)
    del missing['payload']['full_tank']
    assert ev.ingest(cs, who, [missing]).json()['accepted'] == [missing['id']]
    assert _event_row(cs, missing['id'])[2]['full_tank'] is True              # по умолчанию «до полного бака»


def test_refuel_odometer_suspicious_and_supersedes(cs):
    who = _who(cs)
    r1 = _refuel(odo=10000, at='08:00', day='2026-09-28')
    r2 = _refuel(odo=9000, at='08:00', day='2026-09-29')                    # меньше прежнего
    assert ev.ingest(cs, who, [r1, r2]).json()['rejected'] == []
    assert _event_row(cs, r2['id'])[3] == ['odometer_suspicious']
    fix = _refuel(odo=10300, at='08:05', day='2026-09-29', supersedes=r2['id'])   # исправление r2
    assert ev.ingest(cs, who, [fix]).json()['accepted'] == [fix['id']]
    assert _event_row(cs, fix['id'])[3] == []                               # сравнение с r1: r2 вытеснена
    rows = {r['id']: r for r in cs.refuels()}
    assert rows[r2['id']]['superseded'] is True and rows[fix['id']]['superseded'] is False
    fast = _refuel(odo=10300 + 1600, at='09:00', day='2026-09-29')          # +1600 меньше чем за сутки
    far = _refuel(odo=10300 + 4600, at='09:00', day='2026-10-01')           # +4600 за ~2 дня — больше 1500 в сутки
    ev.ingest(cs, who, [fast, far])
    assert _event_row(cs, fast['id'])[3] == ['odometer_suspicious']
    assert _event_row(cs, far['id'])[3] == ['odometer_suspicious']
    near = _refuel(odo=10300 + 4400, at='09:00', day='2026-10-02')          # от fix (подозрительные — мимо): +4400 за ~3 дня
    assert ev.ingest(cs, who, [near]).json()['accepted'] == [near['id']]
    assert _event_row(cs, near['id'])[3] == []
    other = _who(cs, 'CAR2')                                                 # другая машина не влияет
    o = _refuel(odo=5, at='10:00')
    ev.ingest(cs, other, [o])
    assert _event_row(cs, o['id'])[3] == []


def test_refuel_supersedes_cycle_rejected(cs):
    who = _who(cs)
    a, b = _refuel(at='08:00'), _refuel(at='09:00', odo=123500)
    a['payload']['supersedes'] = b['id']                                     # ссылка на ещё не полученное — можно
    b['payload']['supersedes'] = a['id']
    r = ev.ingest(cs, who, [a, b]).json()
    assert r['accepted'] == [a['id']] and r['rejected'][0]['id'] == b['id']
    assert 'շրջան' in r['rejected'][0]['message']


# ============================== миграция courier.db 5 → 6 ==============================

def test_courier_migration_v5_to_v6_keeps_data(cs):
    who = _who(cs)
    e = _refuel()
    assert ev.ingest(cs, who, [e]).json()['accepted'] == [e['id']]
    with closing(sqlite3.connect(cs.path)) as conn:                           # база версии 5: без трека и индекса
        conn.execute('DROP TABLE track_points')
        conn.execute('DROP INDEX events_car_type')
        conn.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
        conn.commit()
    s = Store(cs.path)
    assert [r['id'] for r in s.refuels()] == [e['id']]                       # событие на месте
    with closing(sqlite3.connect(cs.path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)   # 5 → 6 → … → текущая
        names = {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        assert {'track_points', 'track_points_day', 'events_car_type'} <= names
    t = _track([_pt(1)])
    assert ev.ingest(s, who, [t]).json()['accepted'] == [t['id']]


@pytest.mark.skipif(not OWNER_COURIER.exists(), reason='нет courier.db владельца')
def test_courier_owner_copy_migrates_to_v6(tmp_path):
    """КОПИЯ courier.db владельца (схема 5): миграция только добавляет — строки всех таблиц как были. Оригинал
    открывается только на чтение (резервная копия SQLite — согласованная и при работающем сервере)."""
    copy = tmp_path / 'owner_courier.db'
    with closing(sqlite3.connect(f'file:{OWNER_COURIER.as_posix()}?mode=ro', uri=True)) as src,             closing(sqlite3.connect(str(copy))) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(str(copy))) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'")]
        before = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    if version != '5':
        pytest.skip(f'courier.db владельца уже схемы {version}')
    s = Store(str(copy))
    s.refuels()
    with closing(sqlite3.connect(str(copy))) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables} == before
        assert conn.execute('SELECT COUNT(*) FROM track_points').fetchone() == (0,)


# ============================== офис и источник факта ==============================

def test_gps_summary_and_delivered_share():
    assert gps_summary([]) is None
    base = int(datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN).timestamp() * 1000)
    pts = [(base + i * 60000, 40.18 + i * 0.001, 44.5, 5.0) for i in range(11)]   # 10 × ≈111 м
    g = gps_summary(pts)
    assert g['points'] == 11 and g['km'] == pytest.approx(1.1, abs=0.05)
    assert (g['first'], g['last']) == ('2026-10-02T09:00:00+04:00', '2026-10-02T09:10:00+04:00')
    stop = {'lines': [{'qty': 10}, {'qty': 6}, {'qty': True}]}
    assert delivered_share(stop, None) is None
    assert delivered_share(stop, {'payload': {'lines': [{'qty': 8}, {'qty': 0}]}}) == 0.5
    assert delivered_share({'lines': []}, {'payload': {'lines': []}}) is None


def test_office_today_shows_gps_km_and_refuels(st, client, now):
    did, terminal, h = make_terminal(st, car='CAR1')
    s = login(client, h)
    pts = [_pt(i, lat=40.18 + i * 0.001, lon=44.5) for i in range(11)]
    rf = _refuel(odo=1000, at='08:00')
    bad = _refuel(odo=900, at='12:00')
    r = client.post('/api/courier/v1/events', json={'events': [_track(pts), rf, bad]}, headers=s)
    assert r.status_code == 200 and len(r.get_json()['accepted']) == 3
    photo = client.post('/api/courier/v1/photos', headers=s, content_type='multipart/form-data',
                        data={'id': _id(), 'event_id': rf['id'], 'kind': 'photo',
                              'file': (__import__('io').BytesIO(b'\xff\xd8\xff\xe0' + b'\x00' * 100), 'r.jpg')})
    assert photo.status_code == 200, photo.get_json()
    d = client.get(f'/api/courier/admin/today?date={DAY}').get_json()
    car = next(c for c in d['cars'] if c['car_code'] == 'CAR1')
    assert car['gps']['points'] == 11 and car['gps']['km'] == pytest.approx(1.1, abs=0.05)
    assert [x['odometer_km'] for x in car['refuels']] == [1000, 900]
    assert car['refuels'][0]['photos'] and car['refuels'][1]['flags'] == ['odometer_suspicious']
    assert any(f['type'] == 'refuel' and f['flags'] == ['odometer_suspicious'] for f in d['flagged'])
    assert not any(f['type'] == 'track' for f in d['flagged'])


def test_facts_source_day_and_refuels(cs, tmp_path):
    who = _who(cs)
    sid = 'S:%08d-2222-4222-8222-222222222222' % 1
    cs.save_day(DAY, 'CAR1', [{'stop_id': sid, 'seq': 1, 'customer': {'id': 101}, 'lat': SHOP[0], 'lon': SHOP[1],
                               'weight_kg': 500.0, 'lines': [{'line_id': 'a:1', 'qty': 10, 'product_id': 1}]}],
                'v1', DAY + 'T08:00:00+04:00')
    deliv = {'id': _id(), 'type': 'delivery', 'stop_id': sid, 'date': DAY, 'at': DAY + 'T10:30:00+04:00',
             'payload': {'lines': [{'line_id': 'a:1', 'qty': 4}], 'reason_id': 'closed'}}
    assert ev.ingest(cs, who, [_track([_pt(1), _pt(2)]), deliv, _refuel()]).json()['rejected'] == []
    src = FactsSource(cs)
    assert src.car_days('2026-10-01', '2026-10-03') == [('CAR1', DAY)]
    day = src.day('CAR1', DAY)
    assert len(day['track']) == 2 and len(day['track'][0]) == 5
    assert day['stops'] == [{'stop_id': sid, 'customer_id': 101, 'name': None, 'lat': SHOP[0], 'lon': SHOP[1],
                             'weight_kg': 500.0, 'seq': 1, 'delivered_share': 0.4,
                             'delivered_at': DAY + 'T10:30:00+04:00'}]
    assert [r['car_code'] for r in src.refuels()] == ['CAR1']
    missing = FactsSource(Store(str(tmp_path / 'nope' / 'courier.db')))
    assert (missing.car_days('a', 'b'), missing.day('CAR1', DAY), missing.refuels()) == ([], {'track': [], 'stops': []}, [])
    assert not (tmp_path / 'nope' / 'courier.db').exists()


# ============================== ревью learning-loop: H2, L6, M3, предел 40 000 ==============================

def test_h2_correction_counts_at_original_time_and_flags_recomputed(cs):
    """Исправление заправки пришло после следующей: момент — исходной заправки; флаг пересчитывается при чтении."""
    from courier.facts import refuel_flags
    from route_optimizer import learning as lr
    who = _who(cs)
    r1 = _refuel(odo=10000, at='08:00', day='2026-09-28')
    r2 = _refuel(odo=10900, at='08:00', day='2026-09-29')                    # опечатка: на самом деле 10400
    r3 = _refuel(odo=10800, at='08:00', day='2026-09-30')
    assert ev.ingest(cs, who, [r1, r2, r3]).json()['rejected'] == []
    assert _event_row(cs, r3['id'])[3] == ['odometer_suspicious']              # при приёме: 10800 < 10900
    fix = _refuel(odo=10400, at='09:00', day='2026-10-01', supersedes=r2['id'])
    assert ev.ingest(cs, who, [fix]).json()['accepted'] == [fix['id']]
    assert _event_row(cs, fix['id'])[3] == []                                  # сравнение в момент r2 — согласовано
    rows = {r['id']: r for r in cs.refuels()}
    assert rows[fix['id']]['eff_at_utc'] == rows[r2['id']]['at_utc'] and rows[fix['id']]['eff_date'] == '2026-09-29'
    assert refuel_flags(cs.refuels())[r3['id']] == []                          # офис: r3 после исправления — в порядке
    ivs = lr.fuel_intervals(cs.refuels())
    assert [(iv.start.date().isoformat(), iv.end.date().isoformat(), iv.km, iv.liters) for iv in ivs] == [
        ('2026-09-28', '2026-09-29', 400.0, 45.0), ('2026-09-29', '2026-09-30', 400.0, 45.0)]


def test_h2_first_ever_typo_does_not_poison_later_refuels(cs):
    from courier.facts import refuel_flags
    from route_optimizer import learning as lr
    who = _who(cs)
    first = _refuel(odo=1234560, at='08:00', day='2026-09-20')                 # лишняя цифра (на самом деле 123456)
    rest = [_refuel(odo=123456 + 300 * (i + 1), at='08:00', day=f'2026-09-{21 + i}') for i in range(6)]
    assert ev.ingest(cs, who, [first] + rest).json()['rejected'] == []
    flags = refuel_flags(cs.refuels())
    assert flags[first['id']] == ['odometer_suspicious'] and all(flags[e['id']] == [] for e in rest)
    assert len(lr.fuel_intervals(cs.refuels())) == 5
    assert [_event_row(cs, e['id'])[3] for e in rest[1:]] == [[]] * 5           # и при приёме — уже со 2-й правильной


def test_h2_odometer_plausible_chain_rule():
    from route_optimizer.learning import odometer_plausible
    t = [datetime(2026, 9, 1, tzinfo=clock.YEREVAN) + timedelta(days=i) for i in range(6)]
    assert odometer_plausible(list(zip(t, [1000, 1300, 1600, 1500, 1800]))) == [True, True, False, False, True]
    assert odometer_plausible(list(zip(t, [1000, 1300, 16000, 1600, 1800]))) == [True, True, False, True, True]
    assert odometer_plausible(list(zip(t, [1000, 900]))) == [False, False]     # двое несовместимы — неизвестно кто
    assert odometer_plausible(list(zip(t, [1000, 'x', None, 1100]))) == [True, False, False, True]
    assert odometer_plausible([]) == [] and odometer_plausible([(t[0], 5)]) == [True]


def test_l6_purge_track_only_and_by_point_moment(cs):
    """Хранение 400 дней — только трек (по моменту точки, любой машины, и без терминала); доставки и оплаты не
    удаляются."""
    who = _who(cs)
    old_day = (NOW.date() - timedelta(days=TRACK_KEEP_DAYS + 5)).isoformat()
    old_ms = int(datetime.fromisoformat(old_day + 'T10:00:00+04:00').timestamp() * 1000)
    with closing(sqlite3.connect(cs.path)) as conn:
        conn.executemany('INSERT INTO track_points VALUES(?, ?, ?, 40.18, 44.5, 5, NULL, NULL)',
                         [('NOTERM', old_ms, DAY), ('NOTERM', old_ms + 1, old_day)])   # машина без терминала
        for i, etype in enumerate(('delivery', 'payment', 'tare', 'refuel', 'track')):
            conn.execute("INSERT INTO events(id, terminal_id, driver_id, car_code, date, stop_id, type, at_device, at_utc, "
                         "received_at, payload) VALUES(?, 1, 1, 'CAR1', ?, NULL, ?, 'x', 'x', 'x', '{}')",
                         (f'old{i}', old_day, etype))
        conn.commit()
    assert ev.ingest(cs, who, [_track([_pt(1)])]).json()['rejected'] == []
    with closing(sqlite3.connect(cs.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM track_points WHERE car_code = 'NOTERM'").fetchone() == (0,)
        assert sorted(r[0] for r in conn.execute("SELECT type FROM events WHERE id LIKE 'old%'")) == \
            ['delivery', 'payment', 'refuel', 'tare']


def test_track_payload_between_20k_and_40k_accepted_above_40k_rejected(cs):
    who = _who(cs)
    pts = [{**_pt(i // 60, sec=i % 60), 'provider': 'fused-' + 'x' * 140} for i in range(100)]
    size = len(json.dumps({'points': pts}, ensure_ascii=False))
    assert ev.MAX_PAYLOAD_BYTES < size < ev.MAX_TRACK_PAYLOAD_BYTES
    ok = _track(pts)
    assert ev.ingest(cs, who, [ok]).json()['accepted'] == [ok['id']]            # лишние поля точки — не ошибка
    big = _track([{**p, 'provider': 'x' * 400} for p in pts])
    assert len(json.dumps(big['payload'], ensure_ascii=False)) > ev.MAX_TRACK_PAYLOAD_BYTES
    r = ev.ingest(cs, who, [big]).json()
    assert r['accepted'] == [] and 'մեծ' in r['rejected'][0]['message']
    other = {'id': _id(), 'type': 'day_closed', 'stop_id': None, 'date': DAY, 'at': f'{DAY}T18:00:00+04:00',
             'payload': {'summary': {'note': 'x' * 25_000}}}
    assert 'մեծ' in ev.ingest(cs, who, [other]).json()['rejected'][0]['message']   # прочим типам — прежние 20 000


@pytest.mark.parametrize('jitter, spd', [(6e-4, 0.0), (2.6e-4, None)])
def test_m3_office_gps_km_ignores_jitter_at_stops(jitter, spd):
    """Дрожание у магазина км не добавляет: терминал шлёт скорость 0 — при ±60 м; без скорости — в пределах дрожания
    стоя (±29 м: до 73 м между точками, actuals.STAND_JITTER_M — 75 м). Без скорости дрожание больше — уже движение
    (№60), и стоянку у точки дня оно не спасает."""
    import random
    from route_optimizer.geo import haversine_km
    rnd = random.Random(1)
    base = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
    shop = (40.20, 44.52)
    pts, t = [], base
    for i in range(41):                                                    # 40 × ≈ 111 м к магазину, 5 м/с
        pts.append((int(t.timestamp() * 1000), 40.20 - (40 - i) * 0.001, 44.52, 5.0, 5.0))
        t += timedelta(seconds=22)
    for _ in range(30):                                                    # 30 мин у магазина
        t += timedelta(seconds=60)
        pts.append((int(t.timestamp() * 1000), shop[0] + rnd.uniform(-jitter, jitter),
                    shop[1] + rnd.uniform(-jitter, jitter), 8.0, spd))
    path = haversine_km((40.16, 44.52), shop)
    with_stop = gps_summary(pts, [{'stop_id': 'S:1', 'lat': shop[0], 'lon': shop[1]}])
    assert with_stop['km'] == pytest.approx(path, abs=0.15)


def test_m3_office_today_km_ignores_jitter_at_day_stop(st, client, now):
    """Сценарий проверяющего (test_verify_office): офис «Առաքում այսօր» считает км без дрожания у точки дня."""
    import random
    from route_optimizer.geo import haversine_km
    now['t'] = datetime(2026, 10, 2, 20, 0, tzinfo=clock.YEREVAN)
    did = st.store.save_driver(None, 'D', True, '7777', 'admin')
    term_, _ = st.store.create_terminal('U', 'CAR1', 'admin')
    who = ev.Who(term_.id, 'CAR1', did, 'D')
    shop = (40.20, 44.52)
    sid = 'S:%08d-2222-4222-8222-222222222222' % 9
    st.store.save_day(DAY, 'CAR1', [{**_stop(sid, [('a:1', 10, 100.0)]), 'lat': shop[0], 'lon': shop[1], 'weight_kg': 300.0}],
                      'v1', DAY + 'T08:00:00+04:00')
    rnd = random.Random(1)
    t = datetime(2026, 10, 2, 9, 0, tzinfo=clock.YEREVAN)
    pts = []
    for i in range(41):                                                 # 4,4 км к магазину, 5 м/с
        pts.append({'at': clock.iso(t), 'lat': 40.16 + i * 0.001, 'lon': 44.52, 'acc': 5.0, 'spd': 5.0, 'brg': 0.0})
        t += timedelta(seconds=22)
    for _ in range(30):                                                 # 30 мин у магазина, ±29 м, скорости нет
        t += timedelta(seconds=60)
        pts.append({'at': clock.iso(t), 'lat': shop[0] + rnd.uniform(-2.6e-4, 2.6e-4),
                    'lon': shop[1] + rnd.uniform(-2.6e-4, 2.6e-4), 'acc': 8.0, 'spd': None, 'brg': None})
    evs = [{'id': _id(), 'type': 'track', 'stop_id': None, 'date': DAY, 'at': pts[min(k * 100 + 99, len(pts) - 1)]['at'],
            'payload': {'points': pts[k * 100:(k + 1) * 100]}} for k in range((len(pts) + 99) // 100)]
    assert ev.ingest(st.store, who, evs).json()['rejected'] == []
    car = next(c for c in client.get(f'/api/courier/admin/today?date={DAY}').get_json()['cars'] if c['car_code'] == 'CAR1')
    path = haversine_km((40.16, 44.52), shop)
    assert abs(car['gps']['km'] - path) < 0.15


# ============================== верификация, раунд 2: окно флага одометра в офисе ==============================

def test_round2_office_recomputes_refuel_flags_far_back(st, client, now, monkeypatch):
    """Офис за день 250 дней назад, две заправки в день: флаг одометра пересчитан по заправкам вокруг дня (± 200 дней) —
    опечатка помечена, устаревший флаг приёма снят. Прежнее окно («последние 300 до дня + 200 дней») такой день не
    захватывало, и офис показывал флаги приёма. История — синтетические строки Store.refuels (сотни событий через приём
    заняли бы много времени); опечатка — настоящее событие, при приёме других заправок не было — флага нет."""
    day = NOW.date() - timedelta(days=250)
    ds = day.isoformat()
    did = st.store.save_driver(None, 'D', True, '7878', 'admin')
    term_, _ = st.store.create_terminal('U', 'CAR1', 'admin')
    who = ev.Who(term_.id, 'CAR1', did, 'D')
    history, odo = [], 50000
    t = datetime.combine(day, datetime.min.time(), clock.YEREVAN) - timedelta(days=300) + timedelta(hours=7)
    while t < NOW:                                                             # 07:00 и 19:00, +150 км
        odo += 150
        history.append({'id': f'h{len(history):05d}', 'car_code': 'CAR1', 'date': t.date().isoformat(),
                        'at': clock.iso(t), 'at_utc': clock.utc_key(t), 'driver_name': 'D',
                        'payload': {'liters': 30.0, 'odometer_km': odo, 'full_tank': True}, 'flags': [],
                        'superseded': False, 'eff_at_utc': clock.utc_key(t), 'eff_at': clock.iso(t),
                        'eff_date': t.date().isoformat()})
        t += timedelta(hours=12)
    morning, evening = [r for r in history if r['eff_date'] == ds]
    evening['flags'] = ['odometer_suspicious']                                 # устаревший флаг приёма: одометр в порядке
    typo = _refuel(odo=(morning['payload']['odometer_km'] + 75) * 10, at='12:00', day=ds)
    assert ev.ingest(st.store, who, [typo]).json()['rejected'] == []
    assert _event_row(st.store, typo['id'])[3] == []
    real = st.store.refuels
    monkeypatch.setattr(st.store, 'refuels', lambda: real() + history)
    car = next(c for c in client.get(f'/api/courier/admin/today?date={ds}').get_json()['cars'] if c['car_code'] == 'CAR1')
    assert {r['id']: r['flags'] for r in car['refuels']} == {
        morning['id']: [], typo['id']: ['odometer_suspicious'], evening['id']: []}

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
from test_courier import (_fresh_ref_cache, _pin_env, app, client, login, make_terminal, now, st,  # noqa: E402,F401
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
    ({'points': []}, 'points'), ({'points': None}, 'points'), ({}, 'points'), ({'points': 'x'}, 'points'),
    ({'points': [_pt(i % 60, sec=i // 60) for i in range(101)]}, 'points'),
    ({'points': [_pt(1, acc=0), _pt(2, lat=1.0)]}, 'GPS'),
])
def test_track_rejected_whole(cs, payload, message):
    who = _who(cs)
    e = {**_track([]), 'payload': payload}
    r = ev.ingest(cs, who, [e]).json()
    assert r['accepted'] == [] and message in r['rejected'][0]['message']
    assert _points(cs) == []


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
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION) == '6'
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
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '6'
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
    assert day['stops'] == [{'stop_id': sid, 'customer_id': 101, 'lat': SHOP[0], 'lon': SHOP[1], 'weight_kg': 500.0,
                             'seq': 1, 'delivered_share': 0.4}]
    assert [r['car_code'] for r in src.refuels()] == ['CAR1']
    missing = FactsSource(Store(str(tmp_path / 'nope' / 'courier.db')))
    assert (missing.car_days('a', 'b'), missing.day('CAR1', DAY), missing.refuels()) == ([], {'track': [], 'stops': []}, [])
    assert not (tmp_path / 'nope' / 'courier.db').exists()

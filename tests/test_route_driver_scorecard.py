# -*- coding: utf-8 -*-
"""«Վարորդներ» — показатели водителей за период: правила route_optimizer.scorecard (вовремя по ETA и окну, опоздание,
кто закрыл точку, առաքիչ отдельно, км по доле точек, деньги и тара, покрытие), факт людей «Առաքիչ»
(courier.scorecard.CrewSource), API (период, кэш сводок дней по отпечатку) и доступ (администратор; пользователь
территорий и «Гараж» — нет; публичный хост araqich.orix.am не открывает страницу).

Синтетические данные, без ERP; базы — временные. Запуск из корня проекта:
    python -m pytest tests/test_route_driver_scorecard.py -q
"""
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import scorecard as sc  # noqa: E402
from route_optimizer.actuals import YEREVAN as Y  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_live import A, B, C, DEPOT, LIVE_NOW, Track, courier_app  # noqa: E402,F401

D1, D2 = '2026-10-01', '2026-10-02'
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=Y)
T = lambda hh, mm=0: datetime(2026, 10, 1, hh, mm, tzinfo=Y)   # noqa: E731


def mark(arrive, late_min=0.0, early=False, window=False):
    return {'arrive': arrive, 'late_min': late_min, 'early': early, 'window': window}


def cstop(sid, car, status, driver, helper=None, cid=None, name=None):
    return {'stop_id': sid, 'car_code': car, 'status': status, 'driver_id': driver, 'helper_id': helper,
            'customer_id': cid, 'name': name or f'Խանութ {sid}'}


# ============================== правила: вовремя и опоздание ==============================

def test_timing_eta_slack_and_window():
    eta = T(10)
    assert sc.timing(mark(T(10, 15)), eta) == (True, 0.0, None)                  # +15 — ещё вовремя
    assert sc.timing(mark(T(9, 30)), eta) == (True, 0.0, None)                   # раньше плана — вовремя
    assert sc.timing(mark(T(10, 16)), eta) == (False, 16.0, 'eta')               # +16 — опоздал на 16 от плана
    # окно: позже конца окна — опоздание по окну, даже если по плану вовремя
    assert sc.timing(mark(T(10, 5), late_min=5.0, window=True), eta) == (False, 5.0, 'window')
    # и то и другое — берётся большее
    assert sc.timing(mark(T(10, 40), late_min=10.0, window=True), eta) == (False, 40.0, 'eta')
    assert sc.timing(mark(T(10, 40), late_min=55.0, window=True), eta) == (False, 55.0, 'window')
    # обслужил раньше начала окна — не вовремя («рано»), но без опоздания
    assert sc.timing(mark(T(9, 50), early=True, window=True), eta) == (False, 0.0, 'early')
    assert sc.timing(mark(T(9, 50), early=True, window=False), eta) == (True, 0.0, None)   # окна нет — флаг не в счёт
    assert sc.timing(mark(T(10, 10)), eta, slack=5) == (False, 10.0, 'eta')


# ============================== правила: сводка дня ==============================

def _day1():
    crew = {'stops': [cstop('S:A', 'CAR1', 'full', 1, 2, 11), cstop('S:B', 'CAR1', 'partial', 1, 2, 12),
                      cstop('S:C', 'CAR1', 'refused', 1, None, 13), cstop('S:P', 'CAR1', 'pending', None, None, 14),
                      cstop('S:D', 'CAR2', 'full', 3, None, 21), cstop('S:E', 'CAR2', 'covered', None, None, 22),
                      cstop('S:X', 'CAR3', 'full', None, None, 31)],
            'cash': {1: {'expected': 1000.0, 'short': 400.0, 'collected': 600.0, 'handed': 550.0, 'diff': -50.0},
                     3: {'expected': 500.0, 'short': 0.0, 'collected': 500.0, 'handed': None, 'diff': None},
                     4: {'expected': 0.0, 'short': 0.0, 'collected': 200.0, 'handed': None, 'diff': None}},
            'tare': {1: 5.0, 3: 2.0}}
    cars = {'CAR1': sc.CarDay(30.0, {'S:A': mark(T(10, 2)), 'S:B': mark(T(11, 0), late_min=12.0, window=True),
                                     'S:C': mark(T(12))},
                              {11: T(10), 12: T(10, 20)}),
            'CAR9': sc.CarDay(7.5, {}, {})}   # GPS есть, закрытых точек нет — км никому
    return crew, cars


def test_day_summary_people_attribution_and_coverage():
    crew, cars = _day1()
    s = sc.day_summary(crew, cars)
    p = s['people']
    assert set(p) == {'driver:1', 'helper:2', 'driver:3', 'driver:4'}
    d1 = p['driver:1']
    assert (d1['cars'], d1['stops'], d1['partial'], d1['refused']) == (['CAR1'], 3, 1, 1)
    # A вовремя (+2), B — 40 мин от плана (окно — 12), C — без плана: не оценивается
    assert (d1['rated'], d1['on_time'], d1['late_n'], d1['delay_sum']) == (2, 1, 1, 40.0)
    assert d1['late'] == [{'stop_id': 'S:B', 'name': 'Խանութ S:B', 'car': 'CAR1', 'planned': '10:20', 'arrive': '11:00',
                           'delay_min': 40.0, 'reason': 'eta'}]
    assert d1['km'] == 30.0 and d1['tare'] == 5.0 and d1['cash']['short'] == 400.0
    # առաքիչ — отдельной строкой: только точки, где он был; км — его доля среди точек помощников машины (все)
    h = p['helper:2']
    assert (h['stops'], h['partial'], h['refused'], h['rated'], h['on_time'], h['km']) == (2, 1, 0, 2, 1, 30.0)
    assert h['cash'] is None and h['tare'] == 0
    # covered без автора — водителю машины дня (больше всех закрытых точек)
    d3 = p['driver:3']
    assert (d3['stops'], d3['rated'], d3['km'], d3['tare']) == (2, 0, None, 2.0)
    # деньги без точек дня — человек есть, но без точек (в «Օրեր» не считается)
    assert p['driver:4']['stops'] == 0 and p['driver:4']['cash']['collected'] == 200.0
    assert s['coverage'] == {'closed': 6, 'unattributed': 1, 'rated': 2, 'no_eta': 1, 'no_gps': 2, 'car_days': 3,
                             'car_days_gps': 1, 'km_unassigned': 7.5}


def test_day_summary_km_split_between_two_drivers_of_one_car():
    crew = {'stops': [cstop('S:1', 'CAR1', 'full', 1), cstop('S:2', 'CAR1', 'full', 1), cstop('S:3', 'CAR1', 'full', 1),
                      cstop('S:4', 'CAR1', 'full', 2)]}
    p = sc.day_summary(crew, {'CAR1': sc.CarDay(40.0)})['people']
    assert p['driver:1']['km'] == pytest.approx(30.0) and p['driver:2']['km'] == pytest.approx(10.0)
    # ничья по числу точек — водитель машины дня с меньшим id (детерминированно)
    crew['stops'] += [cstop('S:5', 'CAR1', 'full', 2), cstop('S:6', 'CAR1', 'full', 2), cstop('S:7', 'CAR1', 'covered', None)]
    p = sc.day_summary(crew, {})['people']
    assert p['driver:1']['stops'] == 4 and p['driver:2']['stops'] == 3


def test_day_summary_empty_and_helper_equal_to_driver():
    assert sc.day_summary({}, {}) == {'people': {}, 'coverage': {'closed': 0, 'unattributed': 0, 'rated': 0, 'no_eta': 0,
                                                                 'no_gps': 0, 'car_days': 0, 'car_days_gps': 0,
                                                                 'km_unassigned': 0.0}}
    p = sc.day_summary({'stops': [cstop('S:1', 'CAR1', 'full', 1, 1)]}, {})['people']
    assert set(p) == {'driver:1'}   # тот же человек помощником себе — одна строка


# ============================== правила: период ==============================

def test_period_aggregates_days_newest_first():
    crew, cars = _day1()
    day2 = sc.day_summary({'stops': [cstop('S:F', 'CAR1', 'full', 1, None, 11), cstop('S:G', 'CAR1', 'full', 1, None, 12)],
                           'cash': {1: {'expected': 300.0, 'short': -20.0, 'collected': 320.0, 'handed': 320.0, 'diff': 0.0}}},
                          {'CAR1': sc.CarDay(12.25, {'S:F': mark(T(9)), 'S:G': mark(T(10))}, {11: T(9), 12: T(9, 30)})})
    out = sc.period([(date(2026, 10, 2), day2), (date(2026, 10, 1), sc.day_summary(crew, cars))],
                    {1: 'Արամ', 2: 'Բաբկեն', 3: 'Գոռ'})
    rows = {r['key']: r for r in out['drivers']}
    assert [r['key'] for r in out['drivers']] == ['driver:1', 'driver:3', 'driver:4', 'helper:2']   # водители, затем առաքիչ
    r1 = rows['driver:1']
    assert (r1['name'], r1['role'], r1['days'], r1['stops'], r1['rated'], r1['on_time']) == ('Արամ', 'driver', 2, 5, 4, 2)
    assert r1['on_time_pct'] == 50.0 and r1['late'] == 2 and r1['late_mean_min'] == 35.0   # (40 + 30) / 2
    assert r1['km'] == 42.2 and r1['km_days'] == 2 and r1['tare'] == 5.0
    assert r1['cash'] == {'expected': 1300.0, 'short': 380.0, 'collected': 920.0, 'handed': 870.0, 'diff': -50.0,
                          'handed_days': 2}
    assert [d['date'] for d in r1['detail']] == ['2026-10-02', '2026-10-01']
    assert r1['detail'][0]['on_time_pct'] == 50.0 and r1['detail'][0]['late'][0]['delay_min'] == 30.0
    assert rows['driver:4']['days'] == 0 and rows['driver:4']['name'] == '#4'
    assert rows['driver:3']['cash']['handed'] is None and rows['driver:3']['km'] is None
    assert rows['driver:3']['on_time_pct'] is None and rows['driver:3']['late_mean_min'] is None
    assert out['coverage']['closed'] == 8 and out['coverage']['rated'] == 4 and out['coverage']['km_unassigned'] == 7.5
    json.dumps(out)   # всё в JSON (окна и бесконечности сюда не попадают)


# ============================== факт людей: courier.scorecard.CrewSource ==============================

SA = 'S:AAAAAAAA-1111-4111-8111-111111111111'
SB = 'S:BBBBBBBB-2222-4222-8222-222222222222'
SC_ = 'S:CCCCCCCC-3333-4333-8333-333333333333'


def _cash_stop(sid, cid, seq, collect='cash'):
    return {'stop_id': sid, 'seq': seq, 'collect': collect, 'doc_number': str(seq), 'lat': A[0], 'lon': A[1],
            'customer': {'id': cid, 'name': f'Խանութ <b>{cid}</b>'}, 'amount_due': 1000, 'weight_kg': 120.0,
            'lines': [{'line_id': 'l1', 'qty': 10, 'price': 100, 'product_id': 1, 'marked': False, 'weight_kg': 120.0}]}


def _ev(etype, stop_id, payload, at, day, helper=None):
    e = {'id': str(uuid.uuid4()), 'type': etype, 'stop_id': stop_id, 'date': day, 'at': at.isoformat(), 'payload': payload}
    if helper is not None:
        e['helper_id'] = helper
    return e


def test_crew_source_day_versions_and_names(courier_app):
    from courier import events as ev
    from courier.scorecard import CrewSource
    store = courier_app.extensions['courier'].store
    ds = LIVE_NOW.date().isoformat()
    store.save_day(ds, 'CAR1', [_cash_stop(SA, 1, 1), _cash_stop(SB, 2, 2), _cash_stop(SC_, 3, 3, 'none')], 'v1',
                   LIVE_NOW.isoformat())
    did = store.save_driver(None, 'Արամ', True, '1111', 'admin')
    hid = store.save_driver(None, 'Բաբկեն', True, '2222', 'admin')
    terminal, _ = store.create_terminal('U-CAR1', 'CAR1', 'admin')
    with closing(sqlite3.connect(store.path)) as conn, conn:   # առաքիչ подтверждён своим PIN на терминале (crew_log)
        conn.execute("INSERT INTO crew_log(terminal_id, car_code, date, driver_id, helper_id, at_utc, kind) "
                     "VALUES(?, 'CAR1', ?, ?, ?, '2026-10-05T05:00:00Z', 'helper')", (terminal.id, ds, did, hid))
    who = ev.Who(terminal.id, 'CAR1', did, 'Արամ')
    src = CrewSource(store)
    v0 = src.versions(ds, ds)
    t = lambda m: LIVE_NOW - timedelta(minutes=m)   # noqa: E731
    events = [_ev('delivery', SA, {'lines': [{'line_id': 'l1', 'qty': 10}]}, t(60), ds, hid),
              _ev('payment', SA, {'amount': 600, 'kind': 'invoice'}, t(59), ds),
              _ev('tare', SA, {'items': [{'tare_id': 'erp:1', 'qty': 4}, {'tare_id': 'custom:2', 'qty': 1.5}]}, t(58), ds),
              _ev('delivery', SB, {'lines': [{'line_id': 'l1', 'qty': 4}], 'reason_id': 'r1'}, t(40), ds),
              _ev('delivery', SC_, {'lines': [{'line_id': 'l1', 'qty': 0}], 'reason_id': 'r1'}, t(20), ds)]
    r = ev.ingest(store, who, events).json()
    assert len(r['accepted']) == 5, r
    with courier_app.app_context():
        day = src.day(ds)
    assert [(s['stop_id'], s['status'], s['driver_id'], s['helper_id'], s['customer_id']) for s in day['stops']] == [
        (SA, 'full', did, hid, 1), (SB, 'partial', did, None, 2), (SC_, 'refused', did, None, 3)]
    assert day['stops'][0]['name'] == 'Խանութ <b>1</b>' and day['stops'][0]['car_code'] == 'CAR1'
    # «Գումար»: надо взять 1000 (A) + 400 (B, partial), взято 600 — не взято 800; C — collect none
    assert day['cash'] == {did: {'expected': 1400.0, 'short': 800.0, 'collected': 600.0, 'handed': None, 'diff': None}}
    assert day['tare'] == {did: 5.5}
    assert src.names() == {did: 'Արամ', hid: 'Բաբկեն'}
    v1 = src.versions(ds, ds)
    assert v0 == {ds: (0, None, v0[ds][2], 0, None, None)} and v1[ds] != v0[ds]
    store.save_handover(ds, did, 550.0, None, 'boss')   # «сдал фактически» меняет отпечаток дня
    v2 = src.versions(ds, ds)
    assert v2[ds] != v1[ds]
    with courier_app.app_context():
        assert src.day(ds)['cash'][did]['diff'] == -50.0
    assert src.versions('2026-01-01', '2026-01-31') == {}


def test_crew_source_without_db(tmp_path):
    from courier.scorecard import CrewSource
    from courier.store import Store
    src = CrewSource(Store(str(tmp_path / 'none.db')))
    assert src.versions(D1, D2) == {} and src.names() == {} and src.day(D1) == {'stops': [], 'cash': {}, 'tare': {}}
    assert not (tmp_path / 'none.db').exists()


# ============================== API ==============================

class FakeCrew:
    def __init__(self, data):
        self.data = data
        self.ver = {d: (1,) for d in data}
        self.calls = []

    def versions(self, since, until):
        return {d: v for d, v in self.ver.items() if since <= d <= until}

    def day(self, ds):
        self.calls.append(ds)
        return self.data[ds]

    def names(self):
        return {1: 'Արամ', 2: 'Բաբկեն', 3: 'Գոռ <script>'}


class FakeFleet:
    def __init__(self, days):
        self.days = days

    def car_days(self, since, until):
        return sorted((c, d) for c, d in self.days if since <= d <= until)

    def version(self, car, ds):
        return (len(self.days[(car, ds)]['track']),)

    def day(self, car, ds):
        return self.days[(car, ds)]

    def refuels(self, since=''):
        return []


@pytest.fixture
def sc_app(app_v2, monkeypatch):
    """CAR1 1 октября: склад → A (план 5 мин назад) → B (план на 40 мин раньше) → C (нет в плане); водитель 1 и
    առաքիչ 2 на A и B. CAR2 без трека: водитель 3. 2 октября — водитель 3 на CAR1 без трека."""
    from route_optimizer import dispatch as dp
    from route_optimizer import views
    state = app_v2.app.extensions['route_optimizer']
    tr = Track(T(9)).park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B)
    at_b = tr.t
    tr.park(B, 8).drive(C)
    tr.park(C, 8).drive(DEPOT).park(DEPOT, 3)
    fstop = lambda sid, cid, p, seq: {'stop_id': sid, 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': 100.0,   # noqa: E731
                                      'seq': seq, 'delivered_share': 1.0, 'delivered_at': None}
    fleet = FakeFleet({('CAR1', D1): {'track': tr.pts, 'stops': [fstop('S:A', 11, A, 1), fstop('S:B', 12, B, 2),
                                                                 fstop('S:C', 13, C, 3)]}})
    crew = FakeCrew({D1: {'stops': [cstop('S:A', 'CAR1', 'full', 1, 2, 11, 'Խանութ <b>Ա</b>'),
                                    cstop('S:B', 'CAR1', 'partial', 1, 2, 12), cstop('S:C', 'CAR1', 'refused', 1, None, 13),
                                    cstop('S:D', 'CAR2', 'full', 3, None, 21)],
                          'cash': {1: {'expected': 1000.0, 'short': 400.0, 'collected': 600.0, 'handed': None, 'diff': None}},
                          'tare': {1: 3.0}},
                     D2: {'stops': [cstop('S:F', 'CAR1', 'full', 3, None, 11)], 'cash': {}, 'tare': {}}})
    monkeypatch.setattr(state, 'crew_facts', crew)
    monkeypatch.setattr(state, 'fleet_facts', fleet)
    monkeypatch.setattr(state, 'scorecard_cache', {})
    monkeypatch.setattr(state, 'actuals_cache', {})
    monkeypatch.setattr(views, '_yerevan_now', lambda: NOW)
    hm = lambda t: t.strftime('%H:%M')   # noqa: E731
    draft = dp.Draft(trucks=['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [11, 12])])
    draft.prediction = {'trucks': {'CAR1': {'trips': [{'depart': '09:05', 'stops': [
        [11, hm(at_a - timedelta(minutes=5))], [12, hm(at_b - timedelta(minutes=40))]]}]}}}
    state.store.save_dispatch(D1, draft.to_json(), 'qa')
    km = haversine_km(DEPOT, A) + haversine_km(A, B) + haversine_km(B, C) + haversine_km(C, DEPOT)
    return app_v2, crew, km


def test_api_scorecard_for_admin(client, sc_app):
    _, crew, km = sc_app
    _session_as(client, 'boss', base=LAN)
    r = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body['from'], body['to'], body['days'], body['today'], body['gps']) == (D1, D2, 2, '2026-10-07', True)
    assert body['rules'] == {'late_slack_min': 15.0, 'max_days': 92}
    rows = {x['key']: x for x in body['drivers']}
    r1 = rows['driver:1']
    assert (r1['days'], r1['stops'], r1['partial'], r1['refused'], r1['rated'], r1['on_time']) == (1, 3, 1, 1, 2, 1)
    assert r1['on_time_pct'] == 50.0 and r1['late'] == 1 and r1['late_mean_min'] == pytest.approx(40.5, abs=1.0)
    assert r1['km'] == pytest.approx(km, abs=0.2) and r1['cash']['short'] == 400.0 and r1['tare'] == 3.0
    late = r1['detail'][0]['late']
    assert [(x['stop_id'], x['reason'], x['name']) for x in late] == [('S:B', 'eta', 'Խանութ S:B')]
    h2 = rows['helper:2']
    assert (h2['role'], h2['name'], h2['stops'], h2['rated'], h2['on_time']) == ('helper', 'Բաբկեն', 2, 2, 1)
    r3 = rows['driver:3']
    assert (r3['days'], r3['stops'], r3['rated'], r3['km'], r3['name']) == (2, 2, 0, None, 'Գոռ <script>')
    assert [d['date'] for d in r3['detail']] == [D2, D1]
    assert body['coverage'] == {'closed': 5, 'unattributed': 0, 'rated': 2, 'no_eta': 1, 'no_gps': 2, 'car_days': 3,
                                'car_days_gps': 1, 'km_unassigned': 0.0}
    # сводки дней — из кэша: второй запрос факт дня не пересчитывает; изменился отпечаток дня — пересчёт только его
    assert sorted(crew.calls) == [D1, D2]
    assert client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN).get_json() == body
    assert sorted(crew.calls) == [D1, D2]
    crew.ver[D2] = (2,)
    crew.data[D2]['stops'].append(cstop('S:G', 'CAR1', 'full', 3, None, 12))
    again = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN).get_json()
    assert sorted(crew.calls) == [D1, D2, D2] and {x['key']: x for x in again['drivers']}['driver:3']['stops'] == 3
    # правка плана дня — тоже другой отпечаток (ETA другие)
    state = sc_app[0].app.extensions['route_optimizer']
    stored, _ = state.store.load_dispatch(D1)
    state.store.save_dispatch(D1, stored, 'qa')
    client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN)
    assert sorted(crew.calls) == [D1, D1, D2, D2]
    page = client.get('/routes/drivers', base_url=LAN)
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'js/routes_drivers.js?v=1' in html and 'css/routes_drivers.css?v=1' in html
    assert '<a href="/routes/drivers" aria-current="page">Վարորդներ</a>' in html


def test_api_scorecard_period_validation(client, sc_app):
    _session_as(client, 'boss', base=LAN)
    get = lambda q: client.get('/api/routes/drivers/scorecard' + q, base_url=LAN)   # noqa: E731
    body = get('').get_json()
    assert (body['from'], body['to'], body['days']) == ('2026-09-08', '2026-10-07', 30)   # по умолчанию — 30 дней по сегодня
    assert get('?to=2026-10-02').get_json()['from'] == '2026-09-03'
    assert get('?from=2026-07-08&to=2026-10-07').get_json()['days'] == 92                 # ровно предел
    for q in ('?from=2026-07-07&to=2026-10-07', '?from=2026-10-03&to=2026-10-02', '?from=01.10.2026&to=2026-10-02',
              '?from=2026-02-30&to=2026-03-02', '?from=&to=2026-10-02', '?from=2026-10-01&to=2026-10-02%0A'):
        r = get(q)
        assert r.status_code == 400 and r.get_json()['success'] is False and r.get_json()['errors']['date'], q


def test_api_scorecard_without_courier_section(client, sc_app, monkeypatch):
    monkeypatch.setattr(sc_app[0].app.extensions['route_optimizer'], 'crew_facts', None)
    _session_as(client, 'boss', base=LAN)
    assert client.get('/api/routes/drivers/scorecard', base_url=LAN).status_code == 400


def test_api_scorecard_without_gps(client, sc_app, monkeypatch):
    """Обучения («Առաքիչ» без трека) нет — км и «вовремя» не считаются, остальное есть."""
    monkeypatch.setattr(sc_app[0].app.extensions['route_optimizer'], 'fleet_facts', None)
    _session_as(client, 'boss', base=LAN)
    body = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN).get_json()
    r1 = {x['key']: x for x in body['drivers']}['driver:1']
    assert body['gps'] is False and r1['stops'] == 3 and r1['km'] is None and r1['rated'] == 0
    assert body['coverage']['no_gps'] == 5


# ============================== доступ ==============================

def test_scorecard_access_user_garage_anonymous(client, sc_app):
    assert client.get('/api/routes/drivers/scorecard', base_url=LAN).status_code == 401
    r = client.get('/routes/drivers', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']
    _session_as(client, 'u', base=LAN)   # пользователь территорий
    assert client.get('/api/routes/drivers/scorecard', base_url=LAN).status_code == 403
    assert client.get('/routes/drivers', base_url=LAN).status_code == 302   # на свои территории
    _session_as(client, 'garage1', base=LAN)   # «Гараж»: только журнал гаража и карта машин
    assert client.get('/api/routes/drivers/scorecard', base_url=LAN).status_code == 403
    r = client.get('/routes/drivers', base_url=LAN)
    assert r.status_code == 302 and r.headers['Location'].endswith('/routes/garage')


def test_scorecard_not_public(client, sc_app):
    app = sc_app[0]
    for path in ('/routes/drivers', '/api/routes/drivers/scorecard', '/api/routes/drivers'):
        assert app._public_path_allowed(path, 'GET') is False, path
        assert app._garage_path_allowed(path, 'GET') is False, path
    for who in ('boss', 'garage1'):
        _session_as(client, who)
        assert client.get('/routes/drivers', base_url=PUBLIC).status_code == 404, who
        assert client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=PUBLIC).status_code == 404, who
    # через туннель с офисным Host (заголовок Cloudflare) — тоже не видно
    _session_as(client, 'boss', base=LAN)
    r = client.get('/routes/drivers', base_url=LAN, headers={'Cf-Connecting-Ip': '203.0.113.7'})
    assert r.status_code == 404

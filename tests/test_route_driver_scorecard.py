# -*- coding: utf-8 -*-
"""«Վարորդներ» — показатели водителей за период: правила route_optimizer.scorecard (вовремя по ETA и окну, опоздание,
кто закрыл точку, առաքիչ отдельно, км по доле точек, деньги и тара, покрытие; №87: скорость, стоянки вне магазинов,
порядок, литры к норме, балл 0–100 с перенормировкой весов, «քիչ տվյալ» меньше 3 дней, место, точность ETA), факт людей
«Առաքիչ» (courier.scorecard.CrewSource), API (период, кэш сводок дней по отпечатку), литры к норме (_scorecard_fuel),
плитка ETA на «Ուսուցում», доступ (администратор; «Гараж» — без денег, и из интернета; пользователь территорий — нет) и
неделя водителя в APK (GET /api/courier/v1/score: только своя, место без имён).

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
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import scorecard as sc  # noqa: E402
from route_optimizer.actuals import YEREVAN as Y  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from test_garage_public import LAN, PUBLIC, _session_as, app_v2, client  # noqa: E402,F401
from test_route_live import A, B, C, DEPOT, LIVE_NOW, Track, courier_app  # noqa: E402,F401

D1, D2 = '2026-10-01', '2026-10-02'
X = (40.1750, 44.5050)   # не магазин и не склад
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
    assert sc.day_summary({}, {}) == {'people': {}, 'mains': {}, 'eta_errors': [],
                                      'coverage': {'closed': 0, 'unattributed': 0, 'rated': 0, 'no_eta': 0, 'no_gps': 0,
                                                   'car_days': 0, 'car_days_gps': 0, 'km_unassigned': 0.0}}
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
    """CAR1 1 октября: склад → A (план 5 мин назад) → B (план на 40 мин раньше; 108 км/ч по дороге) → стоянка 25 мин
    в X → C (нет в плане); водитель 1 и առաքիչ 2 на A и B. CAR2 без трека: водитель 3. 2 октября — водитель 3 на CAR1
    без трека."""
    from route_optimizer import dispatch as dp
    from route_optimizer import views
    state = app_v2.app.extensions['route_optimizer']
    tr = Track(T(9)).park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 8).drive(B, speed_ms=30.0)   # 108 км/ч целую минуту — одно превышение (> 90 км/ч дольше 30 с)
    at_b = tr.t
    tr.park(B, 8).drive(X).park(X, 25).drive(C)   # 25 мин не у магазина и не на складе — стоянка вне плана
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
    monkeypatch.setattr(state, 'score_week_cache', {})
    monkeypatch.setattr(state, 'actuals_cache', {})
    monkeypatch.setattr(views, '_yerevan_now', lambda: NOW)
    hm = lambda t: t.strftime('%H:%M')   # noqa: E731
    draft = dp.Draft(trucks=['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [11, 12])])
    draft.prediction = {'trucks': {'CAR1': {'trips': [{'depart': '09:05', 'stops': [
        [11, hm(at_a - timedelta(minutes=5))], [12, hm(at_b - timedelta(minutes=40))]]}]}}}
    state.store.save_dispatch(D1, draft.to_json(), 'qa')
    km = haversine_km(DEPOT, A) + haversine_km(A, B) + haversine_km(B, X) + haversine_km(X, C) + haversine_km(C, DEPOT)
    return app_v2, crew, km


def test_api_scorecard_for_admin(client, sc_app):
    _, crew, km = sc_app
    _session_as(client, 'boss', base=LAN)
    r = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body['from'], body['to'], body['days'], body['today'], body['gps']) == (D1, D2, 2, '2026-10-07', True)
    assert body['rules'] == sc.rules() and body['rules']['min_days'] == 3 and body['cash'] is True
    rows = {x['key']: x for x in body['drivers']}
    r1 = rows['driver:1']
    assert (r1['days'], r1['stops'], r1['partial'], r1['refused'], r1['rated'], r1['on_time']) == (1, 3, 1, 1, 2, 1)
    assert r1['on_time_pct'] == 50.0 and r1['late'] == 1 and r1['late_mean_min'] == pytest.approx(40.5, abs=1.0)
    assert r1['km'] == pytest.approx(km, abs=0.2) and r1['cash']['short'] == 400.0 and r1['tare'] == 3.0
    # №87: машина дня — водителю 1 (больше всех точек CAR1); առաքիչ — без этих показателей и без балла
    assert r1['speed_events'] == 1 and r1['speed_per_100km'] == pytest.approx(100.0 / km, abs=0.02)
    assert r1['offroute_min'] == pytest.approx(25, abs=1) and r1['offroute_min_per_day'] == pytest.approx(25, abs=1)
    assert (r1['order_pct'], r1['ordered'], r1['liters_vs_norm_pct']) == (100.0, 2, None)   # заправок нет — литров нет
    assert r1['enough_data'] is False and r1['score'] is None and r1['rank'] is None   # 1 день < 3
    assert set(r1['parts']) == {'on_time', 'order', 'speed', 'stops'}                 # разбивка видна и без балла
    assert sum(p['share'] for p in r1['parts'].values()) == pytest.approx(100.0, abs=0.2)
    d1 = r1['detail'][0]
    assert (d1['speed_events'], d1['order_pct']) == (1, 100.0) and d1['offroute_min'] == pytest.approx(25, abs=1)
    assert body['eta']['n'] == 2 and (body['eta']['within_n'], body['eta']['late_n'], body['eta']['early_n']) == (1, 1, 0)
    assert body['ranked'] == 0
    late = r1['detail'][0]['late']
    assert [(x['stop_id'], x['reason'], x['name']) for x in late] == [('S:B', 'eta', 'Խանութ S:B')]
    h2 = rows['helper:2']
    assert (h2['role'], h2['name'], h2['stops'], h2['rated'], h2['on_time']) == ('helper', 'Բաբկեն', 2, 2, 1)
    assert (h2['speed_events'], h2['offroute_min'], h2['order_pct'], h2['score'], h2['parts']) == (None, None, None, None, {})
    r3 = rows['driver:3']
    assert (r3['days'], r3['stops'], r3['rated'], r3['km'], r3['name']) == (2, 2, 0, None, 'Գոռ <script>')
    assert [d['date'] for d in r3['detail']] == [D2, D1]
    assert body['coverage'] == {'closed': 5, 'unattributed': 0, 'rated': 2, 'no_eta': 1, 'no_gps': 2, 'car_days': 3,
                                'car_days_gps': 1, 'km_unassigned': 0.0,
                                'fuel': {'terrain': 0, 'flat': 0, 'uncovered': 0, 'no_norm': 0}}
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
    assert page.status_code == 200 and 'js/routes_drivers.js?v=2' in html and 'css/routes_drivers.css?v=2' in html
    assert 'data-key="cash"' in html and 'data-cash="1"' in html
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


# ============================== №87: показатели машины, балл, место, точность ETA ==============================

def test_day_summary_car_metrics_go_to_day_driver():
    """Скорость, стоянки и порядок машины — водителю машины дня (больше всех её точек), не второму водителю и не
    առաքիչ; машина без водителя дня (только covered без автора) — никому."""
    crew = {'stops': [cstop('S:1', 'CAR1', 'full', 1, 2), cstop('S:2', 'CAR1', 'full', 1, 2), cstop('S:3', 'CAR1', 'full', 5),
                      cstop('S:4', 'CAR2', 'covered', None)]}
    cars = {'CAR1': sc.CarDay(50.0, speed_events=2, offroute_min=18.0, order=(1, 4)),
            'CAR2': sc.CarDay(20.0, speed_events=7, offroute_min=40.0, order=(3, 3)),
            'CAR3': sc.CarDay(10.0)}
    s = sc.day_summary(crew, cars)
    d1, d5, h2 = s['people']['driver:1'], s['people']['driver:5'], s['people']['helper:2']
    assert (d1['speed_days'], d1['speed_events'], d1['speed_km'], d1['stop_days'], d1['offroute_min'],
            d1['order_changes'], d1['ordered']) == (1, 2, 50.0, 1, 18.0, 1, 4)
    assert (d5['speed_days'], d5['stop_days'], d5['ordered']) == (0, 0, 0)
    assert (h2['speed_days'], h2['stop_days'], h2['ordered']) == (0, 0, 0)
    assert s['mains'] == {'CAR1': 1} and s['coverage']['unattributed'] == 1


def test_day_summary_eta_errors_all_rated_stops():
    """Ошибки ETA — по всем точкам с планом и GPS, и без известного водителя: прибытие − ETA, минуты."""
    crew = {'stops': [cstop('S:A', 'CAR1', 'full', 1, None, 11), cstop('S:B', 'CAR1', 'covered', None, None, 12),
                      cstop('S:Z', 'CAR2', 'covered', None, None, 21), cstop('S:N', 'CAR1', 'full', 1, None, 13)]}
    cars = {'CAR1': sc.CarDay(5.0, {'S:A': mark(T(10, 20)), 'S:B': mark(T(9, 40)), 'S:N': mark(T(12))},
                              {11: T(10), 12: T(10)}),
            'CAR2': sc.CarDay(5.0, {'S:Z': mark(T(11))}, {21: T(11, 30)})}
    s = sc.day_summary(crew, cars)
    assert s['eta_errors'] == [20.0, -20.0, -30.0] and s['coverage']['unattributed'] == 1


def _day(did, n=2, car='CAR1', **car_kw):
    crew = {'stops': [cstop(f'S:{did}:{i}', car, 'full', did) for i in range(n)]}
    return sc.day_summary(crew, {car: sc.CarDay(**car_kw)} if car_kw else {})


def test_period_speed_stops_order_liters_and_score():
    days = [(date(2026, 10, 1), _day(1, km=120.0, speed_events=1, offroute_min=30.0, order=(1, 10))),
            (date(2026, 10, 2), _day(1, km=80.0, speed_events=2, offroute_min=0.0, order=(0, 10))),
            (date(2026, 10, 3), _day(1, km=50.0))]   # трек без показателей: в «на 100 км» и «в день» не входит
    fuel = {('2026-10-01', 'CAR1'): (36.0, 30.0), ('2026-10-02', 'CAR1'): (24.0, 30.0),
            ('2026-10-03', 'CAR2'): (9.0, 1.0)}   # не его машина — не его литры
    out = sc.period(days, {1: 'Արամ'}, fuel)
    r = out['drivers'][0]
    assert (r['speed_events'], r['speed_km'], r['speed_per_100km']) == (3, 200.0, 1.5)
    assert (r['offroute_min'], r['offroute_min_per_day']) == (30, 15.0)
    assert (r['order_pct'], r['order_changes'], r['ordered']) == (95.0, 1, 20)
    assert (r['fuel_days'], r['fuel_fact_l'], r['fuel_norm_l'], r['liters_vs_norm_pct']) == (2, 60.0, 60.0, 0.0)
    det = {d['date']: d for d in r['detail']}
    assert det['2026-10-01']['liters_vs_norm_pct'] == 20.0 and det['2026-10-01']['fuel_fact_l'] == 36.0
    assert det['2026-10-02']['liters_vs_norm_pct'] == -20.0 and det['2026-10-03']['liters_vs_norm_pct'] is None
    assert det['2026-10-03']['speed_events'] is None and det['2026-10-01']['order_pct'] == 90.0
    # 3 дня — балл: вовремя не оценено (выпадает), порядок 95 → 100, скорость 1,5 → 25, стоянки 15 → 75, литры 0 % → 100
    assert r['enough_data'] and r['score'] == pytest.approx((100 * 15 + 25 * 20 + 75 * 15 + 100 * 15) / 65, abs=0.05)
    assert r['parts']['speed'] == {'value': 1.5, 'score': 25.0, 'weight': 20, 'share': 30.8}
    assert 'on_time' not in r['parts'] and r['rank'] == 1 and out['ranked'] == 1
    json.dumps(out)


def test_sub_score_thresholds_and_renormalization():
    assert [sc.sub_score('on_time', v) for v in (100, 95, 72.5, 50, 10)] == [100, 100, 50, 0, 0]
    assert [sc.sub_score('order', v) for v in (100, 90, 70, 50)] == [100, 100, 50, 0]
    assert [sc.sub_score('liters', v) for v in (-30, 5, 15, 25, 60)] == [100, 100, 50, 0, 0]
    assert (sc.sub_score('speed', 0), sc.sub_score('speed', 1), sc.sub_score('speed', 5)) == (100, 50, 0)
    assert (sc.sub_score('stops', 0), sc.sub_score('stops', 30), sc.sub_score('stops', 90)) == (100, 50, 0)
    assert sum(sc.WEIGHTS.values()) == 100
    full, parts = sc.score({'on_time': 95, 'order': 90, 'speed': 0, 'stops': 0, 'liters': 0})
    assert full == 100.0 and [p['share'] for p in parts.values()] == [35.0, 15.0, 20.0, 15.0, 15.0]
    # без скорости и литров веса 35 + 15 + 15 = 65 перенормируются: вовремя — 35 / 65
    got, parts = sc.score({'on_time': 72.5, 'order': 90, 'speed': None, 'stops': 30, 'liters': None})
    assert got == pytest.approx((50 * 35 + 100 * 15 + 50 * 15) / 65, abs=0.05)
    assert set(parts) == {'on_time', 'order', 'stops'} and parts['on_time']['share'] == 53.8
    assert sc.score({'on_time': None}) == (None, {}) and sc.score({}) == (None, {})


def test_min_days_rule_rank_ties_and_helpers():
    """< 3 дней — «քիչ տվյալ»: без балла и места (разбивка есть); равный балл — одно место; առաքիչ — без места."""
    plan = {1: (5, 0), 2: (4, 2), 3: (3, 2), 4: (2, 0)}   # водитель → (дней, точек не по порядку из 10)
    days = []
    for i in range(5):
        crew = {'stops': [cstop(f'S:{d}:{i}', f'CAR{d}', 'full', d, 9 if d == 1 else None)
                          for d, (n, _) in plan.items() if i < n]}
        cars = {f'CAR{d}': sc.CarDay(10.0, order=(bad, 10)) for d, (n, bad) in plan.items() if i < n}
        days.append((date(2026, 10, 5) + timedelta(days=i), sc.day_summary(crew, cars)))
    out = sc.period(days, {})
    rows = {r['key']: r for r in out['drivers']}
    assert [(rows[f'driver:{d}']['score'], rows[f'driver:{d}']['rank']) for d in plan] == [
        (100.0, 1), (75.0, 2), (75.0, 2), (None, None)]
    assert rows['driver:4']['enough_data'] is False and set(rows['driver:4']['parts']) == {'order'}
    assert rows['helper:9']['days'] == 5 and (rows['helper:9']['score'], rows['helper:9']['rank']) == (None, None)
    assert out['ranked'] == 3


def test_eta_accuracy_math():
    e = sc.eta_accuracy([-20.0, -15.0, -3.0, 0.0, 4.5, 15.0, 16.0, 45.0])
    assert (e['n'], e['within_n'], e['early_n'], e['late_n']) == (8, 5, 1, 2)
    assert (e['within_pct'], e['early_pct'], e['late_pct']) == (62.5, 12.5, 25.0)
    # |ошибка| по возрастанию: 0, 3, 4.5, 15, 15, 16, 20, 45 → медиана (15 + 15) / 2; P80 — 16 + 0,6 × (20 − 16)
    assert e['median_abs_min'] == 15.0 and e['p80_abs_min'] == pytest.approx(18.4)
    assert sc.eta_accuracy([]) == {'n': 0, 'ok_min': 15.0, 'within_n': 0, 'within_pct': None, 'early_n': 0,
                                   'early_pct': None, 'late_n': 0, 'late_pct': None, 'median_abs_min': None,
                                   'p80_abs_min': None}


# ============================== литры к норме (views._scorecard_fuel) ==============================

def test_scorecard_fuel_coverage_rule_terrain_and_api(client, sc_app, monkeypatch):
    from route_optimizer import learning, views
    app, _, _ = sc_app
    state = app.app.extensions['route_optimizer']
    bundle = state.store.load()
    d1 = date.fromisoformat(D1)
    kg = views._learning_days(state, bundle, d1, d1)[0][3].km_gps   # км GPS машино-дня — как у расчёта
    ivs = []
    real_norms = views._fuel_norms
    monkeypatch.setattr(views.learning, 'fuel_intervals', lambda refuels, rejected=None: list(ivs))
    monkeypatch.setattr(views, '_track_climbs', lambda st, since, until: {})
    iv = lambda liters, km: learning.Interval('CAR1', T(6), T(21), liters, km)   # noqa: E731
    ivs[:] = [iv(kg * 0.36, kg)]   # трек — весь интервал; расход 36 л/100 против нормы машины 30 (без рельефа)
    fuel, cov = views._scorecard_fuel(state, bundle, d1, d1)
    fact, norm = fuel[(D1, 'CAR1')]
    assert fact == pytest.approx(kg * 0.36) and norm == pytest.approx(kg * 0.30)
    assert cov == {'terrain': 0, 'flat': 1, 'uncovered': 0, 'no_norm': 0}
    ivs[:] = [iv(kg * 0.9, kg * 2.0)]   # трек — ровно половина км интервала: ещё покрыто (правило гаража ≥ 50 %)
    assert views._scorecard_fuel(state, bundle, d1, d1)[1]['flat'] == 1
    ivs[:] = [iv(kg * 0.9, kg * 2.5)]   # 40 % — значения нет
    assert views._scorecard_fuel(state, bundle, d1, d1) == ({}, {'terrain': 0, 'flat': 0, 'uncovered': 1, 'no_norm': 0})
    # рельеф трека (№85): норма — как в «Նորմ և փաստ», км × норма + литры подъёма
    monkeypatch.setattr(views, '_track_climbs', lambda st, since, until: {('CAR1', D1): (150.0, kg)})
    ivs[:] = [iv(kg * 0.36, kg)]
    fuel, cov = views._scorecard_fuel(state, bundle, d1, d1)
    want = views._terrain_norm(10000.0, kg, 30.0, (150.0, kg))
    assert want is not None and want[1] > kg * 0.30 and fuel[(D1, 'CAR1')][1] == pytest.approx(want[1])
    assert cov['terrain'] == 1
    monkeypatch.setattr(views, '_fuel_norms', lambda st, b, before: {})   # у машины нет нормы
    assert views._scorecard_fuel(state, bundle, d1, d1) == ({}, {'terrain': 0, 'flat': 0, 'uncovered': 0, 'no_norm': 1})
    # в API — литры водителю машины дня
    monkeypatch.setattr(views, '_fuel_norms', real_norms)
    monkeypatch.setattr(views, '_track_climbs', lambda st, since, until: {})
    _session_as(client, 'boss', base=LAN)
    body = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D1}', base_url=LAN).get_json()
    r1 = {x['key']: x for x in body['drivers']}['driver:1']
    assert (r1['liters_vs_norm_pct'], r1['fuel_days']) == (20.0, 1) and r1['detail'][0]['liters_vs_norm_pct'] == 20.0
    assert body['coverage']['fuel']['flat'] == 1 and 'liters' in r1['parts']


def test_learning_api_eta_tile(client, sc_app):
    """«Ուսուցում» за период: точность ETA по точкам с планом и GPS (A +5 мин — точно, B +40 — поздно; C без плана)."""
    _session_as(client, 'boss', base=LAN)
    body = client.get(f'/api/routes/learning?from={D1}&to={D1}', base_url=LAN).get_json()
    e = body['eta']
    assert (e['n'], e['within_n'], e['late_n'], e['early_n'], e['ok_min']) == (2, 1, 1, 0, 15.0)
    assert e['median_abs_min'] == pytest.approx(22.5, abs=2.5) and e['within_pct'] == 50.0   # (≈5 + ≈40) / 2
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert 'id="lrEta"' in html and "routes_learning.js') }}?v=18" in html and 'renderEta(d.eta)' in js


# ============================== доступ ==============================

def _keys(obj):
    """Все ключи словарей ответа (рекурсивно)."""
    if isinstance(obj, dict):
        return set(obj).union(*(_keys(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj))
    return set()


def test_scorecard_access_roles_and_cash(client, sc_app):
    url = f'/api/routes/drivers/scorecard?from={D1}&to={D2}'
    assert client.get(url, base_url=LAN).status_code == 401
    r = client.get('/routes/drivers', base_url=LAN)
    assert r.status_code == 302 and '/login' in r.headers['Location']
    _session_as(client, 'u', base=LAN)   # пользователь территорий — раздела нет
    assert client.get(url, base_url=LAN).status_code == 403
    assert client.get('/routes/drivers', base_url=LAN).status_code == 302   # на свои территории
    h = _session_as(client, 'garage1', base=LAN)   # «Гараж»: страница и API — да, денег сервер не отдаёт
    body = client.get(url, base_url=LAN).get_json()
    assert body['success'] and body['cash'] is False and 'cash' not in _keys(body['drivers'])
    assert {x['key']: x for x in body['drivers']}['driver:1']['speed_events'] == 1
    html = client.get('/routes/drivers', base_url=LAN).get_data(as_text=True)
    assert 'data-key="cash"' not in html and 'data-cash="0"' in html and 'Կանխիկ' not in html
    assert 'class="rt-tabs"' not in html and 'href="/routes/garage"' in html
    assert 'href="/routes/drivers"' in client.get('/routes/garage', base_url=LAN).get_data(as_text=True)
    assert client.post(url, base_url=LAN, headers=h).status_code == 403   # только чтение
    _session_as(client, 'boss', base=LAN)
    body = client.get(url, base_url=LAN).get_json()
    assert body['cash'] is True and {x['key']: x for x in body['drivers']}['driver:1']['cash']['short'] == 400.0


def test_scorecard_second_line_role_check(sc_app):
    """Вторая линия (как _admin_only у «Աշխատավարձ»): роль, которой гейт когда-нибудь откроет путь, — всё равно 403."""
    from flask import g
    from route_optimizer import views
    for role in ('user', 'warehouse', None):
        with sc_app[0].app.test_request_context(f'/api/routes/drivers/scorecard?from={D1}&to={D1}'):
            g.user_role = role
            resp, status = views.api_drivers_scorecard()
            assert status == 403 and resp.get_json()['success'] is False, role
            assert views.drivers_page()[1] == 403, role


def test_scorecard_from_internet_garage_only(client, sc_app):
    import re
    app = sc_app[0]
    for path in ('/routes/drivers', '/api/routes/drivers/scorecard', '/static/css/routes_drivers.css',
                 '/static/js/routes_drivers.js'):
        assert app._public_path_allowed(path, 'GET'), path
    for path, method in (('/api/routes/drivers/scorecard', 'POST'), ('/routes/drivers', 'POST'),
                         ('/routes/drivers/', 'GET'), ('/api/routes/drivers/Scorecard', 'GET'),
                         ('/api/routes/drivers-x', 'GET'), ('/api/routes/drivers/../pay', 'GET'),
                         ('/static/js/routes_drivers.js/', 'GET')):
        assert not app._public_path_allowed(path, method), (path, method)
    assert not app._garage_path_allowed('/api/routes/drivers/scorecard', 'POST')
    _session_as(client, 'garage1')   # с телефона через туннель
    page = client.get('/routes/drivers', base_url=PUBLIC)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    own = set(re.findall(r'["\'](/static/[^"\'?]+)', html))
    assert {'/static/css/routes_drivers.css', '/static/js/routes_drivers.js'} <= own
    assert own <= app._PUBLIC_STATIC, own - app._PUBLIC_STATIC   # вся статика страницы открыта снаружи
    body = client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=PUBLIC).get_json()
    assert body['success'] and body['cash'] is False and 'cash' not in _keys(body['drivers'])
    _session_as(client, 'boss')   # администратор снаружи — 404, как весь офис
    assert client.get('/routes/drivers', base_url=PUBLIC).status_code == 404
    assert client.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=PUBLIC).status_code == 404
    _session_as(client, 'boss', base=LAN)   # через туннель с офисным Host (заголовок Cloudflare) — тоже 404
    assert client.get('/routes/drivers', base_url=LAN, headers={'Cf-Connecting-Ip': '203.0.113.7'}).status_code == 404


# ============================== APK: GET /api/courier/v1/score ==============================

API = '/api/courier/v1'
APK_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=Y)   # среда: текущая неделя — с понедельника 05.10


@pytest.fixture
def apk(client, sc_app, tmp_path, monkeypatch):
    """Настоящий app_v2, база «Առաքիչ» — временная: Արամ, Բաբկեն, Գոռ и Դավիթ с PIN, терминалы машин CAR1 и CAR2.
    Неделя «Վարորդներ» — синтетическая (views._scorecard подменён: ровно scorecard.period по сводкам дней): Արամ 3 дня
    в порядке, Բաբկեն 3 дня с 2 точками из 10 не по порядку, Գոռ 2 дня, Դավիթ не работал."""
    import app_v2
    from courier import clock
    from courier import routes_link as rl
    from courier.day import DayService
    from courier.state import CourierState
    from courier.store import Store
    from route_optimizer import views
    store = Store(str(tmp_path / 'courier.db'))
    cs = CourierState(store=store, days=DayService(store, None, lambda d: rl.RoutesView()),
                      public_host='araqich.orix.am', public_url='https://araqich.orix.am/api/courier/v1')
    monkeypatch.setitem(app_v2.app.extensions, 'courier', cs)
    monkeypatch.setattr(clock, 'now', lambda: APK_NOW)
    ids = {name: store.save_driver(None, name, True, pin, 'admin')
           for name, pin in (('Արամ', '1111'), ('Բաբկեն', '2222'), ('Գոռ', '3333'), ('Դավիթ', '4444'))}
    heads = {}
    for car in ('CAR1', 'CAR2'):
        _, token = store.create_terminal(f'U-{car}', car, 'admin')
        heads[car] = {'Authorization': f'Bearer {token}'}
    plan = {ids['Արամ']: (3, 0), ids['Բաբկեն']: (3, 2), ids['Գոռ']: (2, 0)}
    calls = []

    def fake_scorecard(state, since, until):
        calls.append((since, until))
        days = []
        for i in range(3):
            crew = {'stops': [cstop(f'S:{d}:{i}', f'C{d}', 'full', d) for d, (n, _) in plan.items() if i < n]}
            cars = {f'C{d}': sc.CarDay(10.0, order=(bad, 10)) for d, (n, bad) in plan.items() if i < n}
            days.append((since + timedelta(days=i), sc.day_summary(crew, cars)))
        return sc.period(days, {v: k for k, v in ids.items()})
    monkeypatch.setattr(views, '_scorecard', fake_scorecard)

    def login(car, pin):
        r = client.post(f'{API}/login', json={'pin': pin}, headers=heads[car], base_url=LAN)
        assert r.status_code == 200, r.get_json()
        return {**heads[car], 'X-Courier-Session': r.get_json()['session']}
    return SimpleNamespace(ids=ids, heads=heads, calls=calls, login=login,
                           state=sc_app[0].app.extensions['route_optimizer'])


def test_apk_score_own_week_rank_without_names(client, apk, monkeypatch):
    from route_optimizer import views
    aram, babken = apk.login('CAR1', '1111'), apk.login('CAR2', '2222')
    r = client.get(f'{API}/score', headers=aram, base_url=LAN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert set(body) == {'week_from', 'week_to', 'me', 'rank', 'of', 'enough_data', 'min_days'}
    assert (body['week_from'], body['week_to'], body['min_days']) == ('2026-10-05', '2026-10-11', 3)
    me = body['me']
    assert set(me) == {'days', 'stops', 'on_time_pct', 'on_time_n', 'on_time_of', 'avg_late_min', 'order_pct',
                       'speed_events', 'speed_per_100km', 'offroute_stop_min', 'liters_vs_norm_pct', 'score', 'parts'}
    assert (me['days'], me['stops'], me['order_pct'], me['score'], me['on_time_of']) == (3, 3, 100.0, 100.0, 0)
    assert me['parts'] == {'order': {'value': 100.0, 'score': 100.0, 'weight': 15, 'share': 100.0}}
    assert (body['rank'], body['of'], body['enough_data']) == (1, 2, True)   # Գոռ (2 дня) — вне места
    text = r.get_data(as_text=True)
    for other in ('Բաբկեն', 'Գոռ', 'Արամ', '"id"', '"name"', '"key"'):
        assert other not in text, other   # ни имён, ни id — даже своих
    assert apk.calls == [(date(2026, 10, 5), date(2026, 10, 11))]
    # Բաբկեն видит своё и своё место; неделя посчитана один раз на все терминалы (кэш)
    b = client.get(f'{API}/score?week=2026-10-05', headers=babken, base_url=LAN).get_json()
    assert (b['me']['order_pct'], b['me']['score'], b['rank'], b['of']) == (80.0, 75.0, 2, 2) and len(apk.calls) == 1
    # не работал на неделе — пусто, без места
    davit = apk.login('CAR2', '4444')   # новый вход на терминале CAR2 закрывает сессию Բաբկեն
    d = client.get(f'{API}/score', headers=davit, base_url=LAN).get_json()
    assert (d['me']['days'], d['me']['score'], d['rank'], d['of'], d['enough_data']) == (0, None, None, 2, False)
    assert client.get(f'{API}/score', headers=babken, base_url=LAN).status_code == 401
    # кэш недели живёт SCORE_WEEK_TTL_S
    monkeypatch.setattr(views, 'SCORE_WEEK_TTL_S', -1.0)
    client.get(f'{API}/score', headers=aram, base_url=LAN)
    assert len(apk.calls) == 2


def test_apk_score_auth_and_week_validation(client, apk, monkeypatch):
    aram = apk.login('CAR1', '1111')
    # чужая сессия с токеном другого терминала, без сессии, без токена — 401
    stolen = {**apk.heads['CAR2'], 'X-Courier-Session': aram['X-Courier-Session']}
    r = client.get(f'{API}/score', headers=stolen, base_url=LAN)
    assert r.status_code == 401 and r.get_json()['error'] == 'session'
    assert client.get(f'{API}/score', headers=apk.heads['CAR1'], base_url=LAN).get_json()['error'] == 'session'
    assert client.get(f'{API}/score', base_url=LAN).get_json()['error'] == 'unauthorized'
    # неделя — понедельник, не будущая, не раньше 13 недель назад
    for q in ('2026-10-06', '2026-10-12', '2026-06-29', '05.10.2026', '2026-02-30', ''):
        r = client.get(f'{API}/score?week={q}', headers=aram, base_url=LAN)
        assert r.status_code == 400 and r.get_json()['error'] == 'bad_request', q
    assert client.get(f'{API}/score?week=2026-07-06', headers=aram, base_url=LAN).status_code == 200
    assert apk.calls == [(date(2026, 7, 6), date(2026, 7, 12))]
    # «Առաքիչ» не подключён к «Маршрутам» — 503, не пустая оценка
    monkeypatch.setattr(apk.state, 'crew_facts', None)
    r = client.get(f'{API}/score', headers=aram, base_url=LAN)
    assert r.status_code == 503 and r.get_json()['error'] == 'server'

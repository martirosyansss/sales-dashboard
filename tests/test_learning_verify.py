# -*- coding: utf-8 -*-
"""Проверки независимой верификации обучения «Развоза» (238f35f + 6cf2781): кэш факта с настоящей courier.db, потоки,
карта дня, доступ только администратору, выученный расход и профиль часов в «Развозе», плановое ожидание на складе,
отметка доставки после отъезда, точка без фикса GPS, одометр за ограниченное окно. Сценарии — из скриптов проверяющего
(scratchpad/verify-learning), только синтетические данные и временные базы.
Запуск из корня проекта:  python -m pytest tests/test_learning_verify.py -q
"""
import json
import random
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import clock, events as ev  # noqa: E402
from courier.facts import FactsSource, office_window, refuel_flags  # noqa: E402
from courier.store import Store as CourierStore  # noqa: E402
from route_optimizer import actuals as ac, learning as lr, views  # noqa: E402
from test_learning_loop import DEPOT, TODAY, FakeFacts, Track, _learning_client, _stops, _t  # noqa: E402
from test_route_optimizer import DP_DEPOT, _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

TZ = ac.YEREVAN
DAY = TODAY - timedelta(days=1)          # 2026-10-02
DS = DAY.isoformat()
P101, P102, P104 = (40.2100, 44.5600), (40.2000, 44.5300), (40.1800, 44.5200)
_n = iter(range(1, 10 ** 7))


def uid():
    return '%08d-6666-4666-8666-666666666666' % next(_n)


def _sid(k):
    return 'S:%08d-2222-4222-8222-%012d' % (7, k)


def _day_stops(points=(P101, P102, P104), kgs=(400.0, 300.0, 1200.0), names=None):
    cids = (101, 102, 104)
    return [{'stop_id': _sid(k), 'seq': k, 'customer': {'id': cids[k], 'name': (names or {}).get(cids[k], f'Shop {cids[k]}')},
             'lat': p[0], 'lon': p[1], 'weight_kg': kg,
             'lines': [{'line_id': f'{k}:{j}', 'qty': 10, 'product_id': j} for j in range(2)]}
            for k, (p, kg) in enumerate(zip(points, kgs))]


def _track_events(fixes, day=DS):
    pts = [{'at': clock.iso(f.at), 'lat': f.lat, 'lon': f.lon, 'acc': 8.0, 'spd': 6.0, 'brg': 0.0} for f in fixes]
    return [{'id': uid(), 'type': 'track', 'stop_id': None, 'date': day, 'at': pts[min(i + 99, len(pts) - 1)]['at'],
             'payload': {'points': pts[i:i + 100]}} for i in range(0, len(pts), 100)]


def _delivery(k, at, day=DS):
    return {'id': uid(), 'type': 'delivery', 'stop_id': _sid(k), 'date': day, 'at': clock.iso(at),
            'payload': {'lines': [{'line_id': f'{k}:{j}', 'qty': 10} for j in range(2)]}}


@pytest.fixture
def facts(tmp_path, monkeypatch):
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 10, 2, 21, 0, tzinfo=TZ))
    cs = CourierStore(str(tmp_path / 'courier.db'))
    did = cs.save_driver(None, 'D', True, '5555', 'admin')
    term, _ = cs.create_terminal('T', 'CAR1', 'admin')
    who = ev.Who(term.id, 'CAR1', did, 'D')
    cs.save_day(DS, 'CAR1', _day_stops(), 'v1', DS + 'T08:00:00+04:00')
    return cs, who


def _full_track():
    t0 = datetime(DAY.year, DAY.month, DAY.day, 8, 0, tzinfo=TZ)
    tr = Track(DP_DEPOT, t0).stay(20).drive(P101, 30).stay(10).drive(P102, 30).stay(8).drive(DP_DEPOT, 30).stay(25)
    tr.drive(P104, 30).stay(15).drive(DP_DEPOT, 30).stay(5)
    return tr


# ============================== M4: кэш факта и потоки (настоящая courier.db) ==============================

def test_m4_cache_invalidation_with_real_courier_store(client, facts):
    cs, who = facts
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    state.fleet_facts = FactsSource(cs)
    tr = _full_track()
    half = len(tr.fixes) // 2
    assert ev.ingest(cs, who, _track_events(tr.fixes[:half])).json()['rejected'] == []
    calls = []
    real_day = state.fleet_facts.day
    state.fleet_facts.day = lambda car, ds: calls.append(ds) or real_day(car, ds)

    def get():
        rows = views._learning_days(state, views._bundle(state), DAY, DAY)
        assert len(rows) == 1
        return rows[0]

    r0 = get()
    r1 = get()
    assert len(calls) == 1 and r1[3] is r0[3]                                         # из кэша
    assert ev.ingest(cs, who, _track_events(tr.fixes[half:])).json()['rejected'] == []
    r2 = get()                                                                        # остаток трека — пересчёт
    assert len(calls) == 2 and r2[3].points > r0[3].points
    assert all(s.delivered_kg is None for s in r2[2])
    assert ev.ingest(cs, who, [_delivery(0, tr.fixes[0].at + timedelta(minutes=50))]).json()['rejected'] == []
    r3 = get()                                                                        # отметка доставки — пересчёт
    assert len(calls) == 3 and r3[2][0].delivered_kg == 400.0 and r3[2][0].delivered_at is not None
    cs.save_day(DS, 'CAR1', _day_stops(kgs=(450.0, 300.0, 1200.0)), 'v2', DS + 'T09:00:00+04:00')
    r4 = get()                                                                        # новый снимок /day — пересчёт
    assert len(calls) == 4 and r4[2][0].kg == 450.0
    assert client.post('/api/routes/customer-window', json={'customer_id': 102, 'window': {
        'kind': 'after', 't1': 14 * 60, 't2': None, 'tol': None}}).status_code == 200
    r5 = get()                                                                        # окно приёма — пересчёт
    assert len(calls) == 5 and next(s for s in r5[2] if s.customer_id == 102).window is not None
    state.store.save_dispatch(DS, {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [104, 102, 101]}]}, 'qa')
    r6 = get()                                                                        # план — пересчёт
    assert len(calls) == 6 and {s.customer_id: s.rank for s in r6[2]} == {104: 0, 102: 1, 101: 2}
    state.store.save_dispatch(DS, {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [104, 102, 101]}],
                                   'prediction': {'trucks': {'CAR1': {'km': 77.7}}}}, 'qa')
    r7 = get()                                                                        # только прогноз — факт из кэша
    assert len(calls) == 6 and r7[4]['prediction']['trucks']['CAR1']['km'] == 77.7
    assert client.post('/api/routes/settings', json={'depot': {'lat': DP_DEPOT[0] + 0.01, 'lon': DP_DEPOT[1]}}).status_code == 200
    get()                                                                             # склад — пересчёт
    assert len(calls) == 7


def test_m4_cache_thread_safety_under_eviction(client, facts, monkeypatch):
    cs, who = facts
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    state.fleet_facts = FactsSource(cs)
    days = [DAY - timedelta(days=i) for i in range(6)]
    for d in days:
        if d != DAY:
            cs.save_day(d.isoformat(), 'CAR1', _day_stops(), 'v1', d.isoformat() + 'T08:00:00+04:00')
        t0 = datetime(d.year, d.month, d.day, 8, 0, tzinfo=TZ)
        tr = Track(DP_DEPOT, t0).stay(20).drive(P101, 30).stay(10).drive(DP_DEPOT, 30).stay(5)
        assert ev.ingest(cs, who, _track_events(tr.fixes, d.isoformat())).json()['rejected'] == []
    monkeypatch.setattr(views, 'ACTUALS_CACHE_MAX', 2)                                  # кэш постоянно вытесняется
    bundle = views._bundle(state)
    fresh = {(c, d): a for c, d, _, a, *_ in views._learning_days(state, bundle, days[-1], days[0])}
    errors, results = [], []

    def worker(k):
        try:
            for i in range(15):
                a, b = days[(k + i) % 6], days[(k + 2 * i) % 6]
                for c, d, _, act, *_ in views._learning_days(state, bundle, min(a, b), max(a, b)):
                    results.append(act == fresh[(c, d)])
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == [] and results and all(results), (errors[:3], results.count(False))
    assert len(state.actuals_cache) <= 2


# ============================== M6: карта дня ==============================

def test_m6_map_endpoint_edges_and_payload(client, facts):
    cs, who = facts
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    state.fleet_facts = FactsSource(cs)
    evil = '<img src=x onerror=alert(1)>"\'&'
    cs.save_day(DS, 'CAR1', _day_stops(names={101: evil}), 'v9', DS + 'T08:30:00+04:00')
    t0 = datetime(DAY.year, DAY.month, DAY.day, 8, 0, tzinfo=TZ)
    tr = Track(DP_DEPOT, t0).stay(20, step=5)                                          # плотный день: точка в 5 с
    for _ in range(6):
        tr.drive(P101, 25, step=5).stay(20, step=5).drive(P104, 25, step=5).stay(20, step=5)
        tr.drive(DP_DEPOT, 25, step=5).stay(15, step=5)
    assert ev.ingest(cs, who, _track_events(tr.fixes)).json()['rejected'] == []
    r = client.get(f'/api/routes/learning/day?date={DS}&car=CAR1')
    d = r.get_json()
    assert r.status_code == 200 and len(d['track']) <= views.MAP_TRACK_POINTS and len(r.get_data()) < 200_000
    assert d['track_points'] > views.MAP_TRACK_POINTS
    assert next(s['name'] for s in d['stops'] if s['customer_id'] == 101) == evil     # как есть: страница — через esc()
    assert r.mimetype == 'application/json'
    assert d['planned'] == [] and all(s['planned_eta'] is None for s in d['stops'])   # плана на день нет
    r = client.get(f'/api/routes/learning/day?date={DS}&car=NOPE')
    assert r.status_code == 200 and r.get_json()['track'] == [] and r.get_json()['stops'] == []
    r = client.get('/api/routes/learning/day?date=2026-09-01&car=CAR1')
    assert r.status_code == 200 and r.get_json()['track'] == []
    assert client.get(f'/api/routes/learning/day?date={DS}&car=' + 'X' * 21).status_code == 400
    state.fleet_facts = None
    assert client.get(f'/api/routes/learning/day?date={DS}&car=CAR1').status_code == 400


@pytest.fixture
def dashboard(monkeypatch):
    import app_v2
    users = {'u': {'role': 'user', 'areas': ['01'], 'password_hash': 'x'},
             'boss': {'role': 'admin', 'areas': [], 'password_hash': 'x'}}
    monkeypatch.setattr(app_v2, 'load_users', lambda: users)
    return app_v2.app.test_client()


def test_m6_new_learning_endpoints_are_admin_only(dashboard):
    c = dashboard
    paths = [f'/api/routes/learning/day?date={DS}&car=CAR1', '/api/routes/learning/status', '/api/routes/learning']
    anon = {p: c.get(p).status_code for p in paths}
    with c.session_transaction() as s:
        s['username'] = 'u'
    user = {p: c.get(p).status_code for p in paths}
    user_post = c.post('/api/routes/learning/run', json={}).status_code
    with c.session_transaction() as s:
        s['username'] = 'boss'
    admin = {p: c.get(p).status_code for p in paths}
    assert all(v in (401, 302) for v in anon.values())
    assert all(v == 403 for v in user.values()) and user_post == 403
    assert all(v not in (401, 403) for v in admin.values())


# ============================== L10, M5: выученное в «Развозе» ==============================

def test_l10_learned_fuel_l100_and_restore(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']

    def l100s():
        p = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
        return {t['car_code']: t['l100'] for t in p['plan']['trucks']}
    manual = l100s()
    state.store.save_learned('2026-09-01', [lr.Outcome('fuel', 'CAR1', True, 'да', {'empty_l100': 20.0, 'full_l100': 36.0})])
    learned = l100s()
    assert client.post('/api/routes/learning/auto', json={'kind': 'fuel', 'auto': False}).status_code == 200
    off = l100s()
    assert client.post('/api/routes/learning/auto', json={'kind': 'fuel', 'auto': True}).status_code == 200
    assert manual.get('CAR1') == 30 and learned.get('CAR1') == 28.0 and off == manual and l100s() == learned


def test_m5_scored_profile_equals_dispatch_profile(client, monkeypatch):
    state = _learning_client(client, monkeypatch, days=30)
    o = {x.kind: x for x in views.run_learning(state, TODAY)}['travel']
    assert o.params, o.reason
    snap, _ = state.snapshots.cached()
    bundle = views._bundle(state)
    ready = views._ready_trucks(snap, bundle)
    base = views._dispatch_ctx(state, snap, bundle, TODAY, ready, [], {}, learned=False)
    applied, _, _ = lr.apply_learned(base.norms, base.tn, ready, lr.InEffect(travel={**o.params, 'model_id': o.model_id}), {})
    scored = lr.travel_profile(base.norms, o.params)
    probes = [(km, spd, city, wd, start) for km in (2.0, 8.0) for spd in (25.0, 45.0) for city in (True, False)
              for wd in (0, 5) for start in (8 * 60 + 50, 9 * 60 + 55, 13 * 60)]
    assert max(abs(applied.traffic.travel(*p) - scored.travel(*p)) for p in probes) == 0.0


def test_build_stores_prediction_trips(client, monkeypatch):
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 20, 0))       # вечер накануне
    _dispatch_setup(client, [_dorder(1, 101, 4000.0), _dorder(2, 102, 3000.0, agent=2), _dorder(3, 104, 2500.0, agent=2)])
    assert client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR2']}).status_code == 200
    state = client.application.extensions['route_optimizer']
    draft, _ = state.store.load_dispatch('2026-10-01')
    trips = lr.plan_trips(draft['prediction']['trucks']['CAR2'], date(2026, 10, 1))
    assert len(trips) >= 2 and all(t.loading_start is not None and t.depart is not None and t.customers for t in trips)
    assert trips[0].prev_return is None and all(t.prev_return is not None for t in trips[1:])


# ============================== H1: ночное переобучение загрузки ==============================

@pytest.mark.parametrize('start', [(10.0, 2.0), (30.0, 8.0)])
def test_h1_nightly_loading_converges_from_wrong_norm(start):
    """Сценарий проверяющего (probe_h1_dynamics): каждую ночь норма = последняя принятая; стоянки — истинная загрузка
    15 + 5·т ± 2 и иногда обед (+15–40 мин). Шаг ±30% за ночь, нижний квантиль: норма подходит к истине и не уходит
    вниз от раннего возвращения машин (плановое ожидание считается только своё)."""
    rnd = random.Random(1)
    first = date(2026, 3, 1)
    obs = []
    for i in range(240):
        d = first + timedelta(days=i)
        for _ in range(2):
            t = rnd.choice([1.0, 2.0, 3.0, 4.0])
            m = 15 + 5 * t + rnd.uniform(-2, 2) + (rnd.uniform(15, 40) if rnd.random() < 0.2 else 0.0)
            if m <= 60:
                obs.append(lr.LoadObs(d, t, m))
    cur = start
    for night in range(40):
        o = lr.fit_loading(obs, first + timedelta(days=130 + night), cur)
        if o.accepted:
            cur = (o.params['fixed_min'], o.params['per_tonne_min'])
    assert cur[0] + cur[1] * 2.5 == pytest.approx(27.5, abs=2.0)


# ============================== L3, L4: отметка доставки ==============================

def test_l3_delivery_tap_long_after_leaving_does_not_hide_early_arrival():
    """У магазина 13:20–13:40 при окне «после 14:00» (разгрузился рано и уехал), отметил доставку в 17:00 — раньше окна."""
    tr = Track(DEPOT, _t(13)).stay(10).drive((40.2100, 44.5600), 30).stay(20).drive(DEPOT, 30).stay(5)
    d = date(2026, 9, 29)
    honest = _stops(A={'window': (14 * 60, float('inf'))})
    late_tap = _stops(A={'window': (14 * 60, float('inf')), 'delivered_at': _t(17, 0)})
    assert ac.visit_metrics(ac.reconstruct(tr.fixes, honest, DEPOT), honest, d).early == 1
    m = ac.visit_metrics(ac.reconstruct(tr.fixes, late_tap, DEPOT), late_tap, d)
    assert (m.on_time, m.early) == (0, 1)
    # отметка во время стоянки (ждал открытия у магазина) — по ней
    tr = Track(DEPOT, _t(13)).stay(10).drive((40.2100, 44.5600), 30).stay(50).drive(DEPOT, 30).stay(5)
    waited = _stops(A={'window': (14 * 60, float('inf')), 'delivered_at': _t(14, 3)})
    assert ac.visit_metrics(ac.reconstruct(tr.fixes, waited, DEPOT), waited, d).on_time == 1


def test_l4_no_fix_stop_tapped_after_return_falls_back_to_plan_order():
    """Точка B без фикса GPS, а доставку водитель отметил вечером, после возвращения (вне рейса): груз — по плану."""
    tr = Track(DEPOT, _t(8, 30)).stay(20).drive((40.2100, 44.5600), 30).stay(10)
    tr.drive((40.2000, 44.5300), 30, gap=(0, 10 ** 6)).stay(8, gap=(0, 10 ** 6))
    tr.drive((40.1800, 44.5200), 30).stay(10).drive(DEPOT, 30).stay(5)
    stops = _stops(B={'delivered_at': _t(18, 30)})
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    assert [t.loaded_kg for t in day.trips] == [3500.0] and day.trips[0].unseen == ('B',)
    assert [round(kg) for _, _, kg in ac.load_profile(day, stops)] == [3500, 2500, 0]


# ============================== п. 6: одометр за ограниченное окно ==============================

def _full_dp(items):
    """Эталон: та же цепочка без ограничения соседей (O(n²))."""
    old = lr.REFUEL_LOOKBACK
    lr.REFUEL_LOOKBACK = len(items) + 1
    try:
        return lr.odometer_plausible(items)
    finally:
        lr.REFUEL_LOOKBACK = old


def test_odometer_lookback_matches_full_chain_and_is_fast():
    rnd = random.Random(3)
    base = datetime(2024, 1, 1, tzinfo=TZ)
    for n in (150, 400):
        odo, items = 100000, []
        for i in range(n):
            odo += rnd.randint(150, 600)
            o = odo * 10 if rnd.random() < 0.02 else (odo // 10 if rnd.random() < 0.01 else odo)   # опечатки
            items.append((base + timedelta(days=i * 2), o))
        assert lr.odometer_plausible(items) == _full_dp(items)
    odo, items = 100000, []
    for i in range(2000):
        odo += rnd.randint(150, 600)
        items.append((base + timedelta(days=i), odo * 10 if rnd.random() < 0.01 else odo))
    t = time.perf_counter()
    ok = lr.odometer_plausible(items)
    assert time.perf_counter() - t < 1.0 and ok.count(False) >= 10


def test_refuel_window_bounds_chain_and_ingest(tmp_path, monkeypatch):
    rows = [{'id': f'r{i:04d}', 'car_code': 'CAR1', 'at_utc': (datetime(2024, 1, 1, tzinfo=TZ) + timedelta(days=i)).isoformat(),
             'payload': {'odometer_km': 1000 + 100 * i, 'liters': 20.0}, 'superseded': False} for i in range(700)]
    assert len(lr.effective_refuels(rows)['CAR1']) == 700                       # окно задаёт вызывающий, по времени
    mid = datetime(2024, 1, 1, tzinfo=TZ) + timedelta(days=100)
    around = lr.effective_refuels(rows, mid - timedelta(days=10), mid)['CAR1']
    assert (around[0][1], around[-1][1], len(around)) == ('r0090', 'r0100', 11)
    assert set(refuel_flags(rows, mid, mid)) == {r['id'] for r in rows}        # флаги есть у всех, пересчёт — в окне
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 10, 2, 12, 0, tzinfo=TZ))
    cs = CourierStore(str(tmp_path / 'c.db'))
    did = cs.save_driver(None, 'D', True, '5656', 'admin')
    term, _ = cs.create_terminal('T', 'CAR1', 'admin')
    who = ev.Who(term.id, 'CAR1', did, 'D')

    def rf(day, odo):
        return {'id': uid(), 'type': 'refuel', 'stop_id': None, 'date': day, 'at': f'{day}T08:00:00+04:00',
                'payload': {'liters': 40.0, 'odometer_km': odo, 'full_tank': True}}
    old_typo = rf('2025-01-10', 9_999_999 // 10)                                   # больше года назад: вне окна
    good = [rf(f'2026-09-{d:02d}', 50000 + 300 * d) for d in range(1, 11)]
    assert ev.ingest(cs, who, [old_typo] + good).json()['rejected'] == []
    flags = {r['id']: r['flags'] for r in cs.refuels()}
    assert all(flags[e['id']] == [] for e in good)                                 # старая опечатка вне окна не мешает
    assert json.dumps(flags)                                                       # и сохранённые флаги читаются


# ============================== раунд 2: окно заправок — по периоду вызывающего ==============================

def _daily_refuels(per_day, days, typo=None):
    """Строки Store.refuels машины CAR1 до вчера: per_day заправок в день «до полного бака»; typo — номер с опечаткой."""
    rnd = random.Random(3)
    out, odo, i = [], 100000, 0
    t = datetime(TODAY.year, TODAY.month, TODAY.day, 7, 0, tzinfo=TZ) - timedelta(days=days)
    while t.date() < TODAY:
        odo += rnd.randint(60, 300) / per_day
        o = round(odo) * 10 if i == typo else round(odo)
        u = t.astimezone(timezone.utc).isoformat()
        out.append({'id': f'r{i:05d}', 'car_code': 'CAR1', 'date': t.date().isoformat(), 'at': t.isoformat(), 'at_utc': u,
                    'eff_at_utc': u, 'eff_at': t.isoformat(), 'eff_date': t.date().isoformat(),
                    'payload': {'liters': 30.0, 'odometer_km': o, 'full_tank': True}, 'flags': [], 'superseded': False})
        t += timedelta(days=1) / per_day
        i += 1
    return out


def _full_history(fn, *args):
    """Как в 6cf2781: вся история заправок — без окна (REFUEL_WINDOW_*) и без ограничения соседей в цепочке."""
    saved = lr.REFUEL_LOOKBACK, lr.REFUEL_WINDOW_DAYS, lr.REFUEL_WINDOW_MAX
    lr.REFUEL_LOOKBACK, lr.REFUEL_WINDOW_DAYS, lr.REFUEL_WINDOW_MAX = 10 ** 6, 36500, 10 ** 6   # дни: 100 лет
    try:
        return fn(*args)
    finally:
        lr.REFUEL_LOOKBACK, lr.REFUEL_WINDOW_DAYS, lr.REFUEL_WINDOW_MAX = saved


def test_round2_report_far_back_keeps_refuel_liters(client, monkeypatch):
    """Отчёт за дни 320 дней назад при ежедневных заправках: литры есть и те же, что по всей истории (6cf2781)."""
    state = _learning_client(client, monkeypatch, days=3)
    refs = _daily_refuels(1.0, 800)
    ref_ivs = _full_history(lr.fuel_intervals, refs)
    for back in (30, 200, 320):
        days = [TODAY - timedelta(days=back - k) for k in range(5)]
        state.fleet_facts = FakeFacts(days)
        state.fleet_facts.refuels = lambda refs=refs: refs
        got = client.get(f'/api/routes/learning?from={days[0].isoformat()}&to={days[-1].isoformat()}').get_json()
        liters = {x['day']: x['fact']['liters'] for x in got['days']}
        assert all(v is not None for v in liters.values()), (back, liters)
        for x in got['days']:
            l100 = lr.daily_l100(ref_ivs, 'CAR1', date.fromisoformat(x['day']))
            # в ответе км округлены до 0,1, литры считаются от неокруглённых: допуск — два округления
            assert x['fact']['liters'] == pytest.approx(x['fact']['km'] * l100 / 100, abs=0.05 + 0.05 * l100 / 100 + 1e-9)


@pytest.mark.parametrize('per_day', [0.5, 1.0, 2.0, 3.0])
def test_round2_learning_refuel_intervals_unchanged(per_day):
    """Обучение: интервалы заправок за период обучения — все (и в первую неделю), как по всей истории (6cf2781)."""
    refs = _daily_refuels(per_day, 800)
    train_from, _ = lr.windows(TODAY)
    sel = [r for r in refs if r['eff_date'] >= (train_from - timedelta(days=30)).isoformat()]   # как run_learning
    got = lr.fuel_intervals(sel)
    assert got == _full_history(lr.fuel_intervals, sel) == _full_history(lr.fuel_intervals, refs)[-len(got):]
    odo = [r['payload']['odometer_km'] for r in sel]
    assert len(got) == sum(1 for a, b in zip(odo, odo[1:]) if b - a >= lr.FUEL_MIN_KM)      # каждая пара соседних

    def first_week(ivs):
        return sum(1 for iv in ivs if train_from <= iv.end.astimezone(TZ).date() < train_from + timedelta(days=7))
    assert first_week(got) == first_week(_full_history(lr.fuel_intervals, refs)) >= 3


@pytest.mark.parametrize('per_day, back', [(1.0, 60), (1.0, 250), (2.0, 120), (2.0, 250)])
def test_round2_office_flags_typo_far_back(per_day, back):
    refs = _daily_refuels(per_day, 800)
    day = TODAY - timedelta(days=back)
    target = next(i for i, r in enumerate(refs) if r['eff_date'] == day.isoformat())
    refs = _daily_refuels(per_day, 800, typo=target)
    flags = refuel_flags(refs, *office_window(day))
    assert flags[refs[target]['id']] == ['odometer_suspicious']
    assert all(flags[r['id']] == [] for r in refs if r['eff_date'] == day.isoformat() and r is not refs[target])


def test_round2_ingest_flag_matches_full_history(tmp_path, monkeypatch):
    """Флаг при приёме (окно ± 400 дней, не больше 300) — как по всей истории, известной серверу в момент приёма."""
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 10, 2, 23, 0, tzinfo=TZ))
    cs = CourierStore(str(tmp_path / 'edges.db'))
    did = cs.save_driver(None, 'D', True, '8181', 'admin')
    term, _ = cs.create_terminal('T', 'CAR1', 'admin')
    who = ev.Who(term.id, 'CAR1', did, 'D')
    rnd = random.Random(21)
    start = datetime(2026, 3, 1, tzinfo=TZ)
    odo, mism = 100000, []
    for i in range(150):
        odo += rnd.randint(100, 400)
        o = (odo * 10 if rnd.random() < 0.6 else odo // 10) if rnd.random() < 0.03 else odo
        d = (start + timedelta(days=i)).date().isoformat()
        e = {'id': uid(), 'type': 'refuel', 'stop_id': None, 'date': d, 'at': f'{d}T08:00:00+04:00',
             'payload': {'liters': 40.0, 'odometer_km': o, 'full_tank': True}}
        assert ev.ingest(cs, who, [e]).json()['rejected'] == []
        rows = cs.refuels()
        items = sorted((datetime.fromisoformat(r['eff_at_utc']), r['id'], r['payload'].get('odometer_km'))
                       for r in rows if not r['superseded'])
        ok = dict(zip((x[1] for x in items), _full_history(lr.odometer_plausible, [(t, v) for t, _, v in items])))
        stored = 'odometer_suspicious' not in next(r['flags'] for r in rows if r['id'] == e['id'].lower())
        if stored != ok[e['id'].lower()]:
            mism.append(i)
    assert mism == []

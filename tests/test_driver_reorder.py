# -*- coding: utf-8 -*-
"""Срок магазина у водителя и «Գնալ առաջինը» (ответ владельца №93, контракт «Առաքիչ» v1.8 §12).

- «Развоз»: настройка until_buffer_min (15, 0…120); dispatch.deadline_windows; сборка, перестановка под срок (fit_until) и
  план (window_tight «քիչ ժամանակ կա» / window_miss «չի հասցնում»), пересборка магазина, которому мешает только запас;
- /day: until / until_from точки, trips с матрицей рейса (courier.day.trips_json, _matrix), until_buffer_min, вид
  «Маршрутов» (routes_link) и дорожная модель (route_optimizer.views.trip_road);
- событие reorder: проверки, no_deadline, идемпотентность, Store.reorders, факт карты и обучения;
- эталон порядка: actuals.reorders_of / reorder_trip / reordered_changes («Վարորդներ»), live.sequence_check и
  reordered_plan (карта машин), карточка машины (reorders, следующий магазин и плановое ETA по новому порядку), API
  «Վարորդներ».

Синтетические данные, без ERP и без карты дорог. Запуск:  python -m pytest tests/test_driver_reorder.py -q
"""
import json
import math
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import day as dy  # noqa: E402
from courier import routes_link as rl  # noqa: E402
from courier.facts import FactsSource  # noqa: E402
from courier.live import LiveSource  # noqa: E402
from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import live  # noqa: E402
from route_optimizer import store as rst  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from test_courier import DEMO, app, client, event, login, make_terminal, now, post, st  # noqa: E402,F401
from test_route_dispatch_until import HOWO, FORD, _info, _setup, _stops_of  # noqa: E402
from test_route_live import A, B, C, DAY as LDAY, DEPOT, T0, Track, facts, stop, view  # noqa: E402

INF = math.inf


@pytest.fixture(autouse=True)
def _fresh_matrices():
    """Кэш матриц рейсов /day (courier.day) — модульный: между тестами не переносится."""
    dy.clear_matrix_cache()
    yield
    dy.clear_matrix_cache()
D = (40.19, 44.52)   # четвёртый магазин карты


def T(m):
    return T0 + timedelta(minutes=m)


# ============================== «Развоз»: запас до срока ==============================

def test_setting_default_and_validation():
    assert rst.DEFAULT_SETTINGS['until_buffer_min'] == 15
    base = dict(rst.DEFAULT_SETTINGS)
    out, errors = rst.validate_settings(base, None)
    assert not errors and out['until_buffer_min'] == 15
    for ok in (0, 30, 120):
        assert rst.validate_settings({**base, 'until_buffer_min': ok}, None)[0]['until_buffer_min'] == ok
    for bad in (-1, 121, None, '15'):
        assert 'until_buffer_min' in rst.validate_settings({**base, 'until_buffer_min': bad}, None)[1], bad


def test_deadline_windows():
    spans = {1: (-INF, 600.0), 2: (600.0, 610.0), 3: (540.0, INF), 4: (650.0, 700.0)}
    windows, real = dp.deadline_windows(spans, 15)
    assert windows == {1: (-INF, 585.0), 2: (600.0, 600.0), 3: (540.0, INF), 4: (650.0, 685.0)}
    assert real == {1: 600.0, 2: 610.0, 4: 700.0}             # «после 09:00» — без конца, запаса нет
    assert dp.deadline_windows(spans, 0) == (spans, {})       # 0 — окна как есть


def _first_eta(ctx, stops, cid):
    """Минута прибытия к магазину cid, если он первый в рейсе HOWO (от полуночи)."""
    routable = {s.customer_id: s for s in stops}
    order = [cid] + [c for c in (101, 102, 103, 104) if c != cid]
    trips = [dp.DraftTrip(1, HOWO.car_code, order)]
    return 540 + dp._timeline(ctx, trips, routable, dp._shares(trips))[1][2][0]


def _with_deadline(ctx, cid, end, buffer=15):
    windows, real = dp.deadline_windows({cid: (-INF, end)}, buffer)
    return replace(ctx, windows=windows, until_real=real)


def test_fit_until_targets_buffer_when_possible():
    """Срок 104 позже её ETA, но ETA позже срока − 15: перестановка — к сроку с запасом; запас выходит — не tight."""
    ctx, stops, draft, _ = _setup()
    eta = 540 + dp._timeline(ctx, draft.trips, {s.customer_id: s for s in stops}, dp._shares(draft.trips))[1][2][3]
    ctx = _with_deadline(ctx, 104, eta + 5)
    got = dp.fit_until(ctx, stops, draft, 104)
    assert got == {'late': False, 'reordered': [1], 'kept': [], 'split': False, 'hint': None, 'tight': False}
    s = _stops_of(dp.plan_view(ctx, stops, draft, _info, explain=False))[104][1]
    assert s['window_miss'] is False and 'window_tight' not in s and s['margin_min'] >= 15


def test_fit_until_tight_when_only_real_deadline_is_feasible():
    """С запасом не успеть нигде (срок = прибытие первым + 5 мин, запас 15), а в срок — да: перестановка к настоящему
    сроку, ответ tight; в плане — жёлтое window_tight с запасом 5 мин, не window_miss."""
    ctx, stops, draft, _ = _setup()
    first = _first_eta(ctx, stops, 104)
    ctx = _with_deadline(ctx, 104, first + 5)
    got = dp.fit_until(ctx, stops, draft, 104)
    assert (got['late'], got['tight'], got['reordered'], got['hint']) == (False, True, [1], None)
    assert draft.trips[0].stops[0] == 104
    view_ = dp.plan_view(ctx, stops, draft, _info, explain=False)
    s = _stops_of(view_)[104][1]
    assert s['window_miss'] is False and s['window_tight'] is True and s['margin_min'] == pytest.approx(5, abs=0.1)
    trip = next(tr for t in view_['trucks'] for tr in t['trips'] if tr['id'] == 1)
    assert trip['window_miss'] == 0 and trip['window_tight'] == 1


def test_fit_until_red_only_when_real_deadline_missed():
    ctx, stops, draft, _ = _setup()
    ctx = _with_deadline(ctx, 104, _first_eta(ctx, stops, 104) - 5)
    got = dp.fit_until(ctx, stops, draft, 104)
    assert (got['late'], got['tight']) == (True, False)
    s = _stops_of(dp.plan_view(ctx, stops, draft, _info, explain=False))[104][1]
    assert s['window_miss'] is True and 'window_tight' not in s


def test_no_buffer_keeps_fit_answer_and_plan_keys():
    """Запаса нет (окно без конца или настройка 0) — ответ fit_until и ключи плана прежние."""
    ctx, stops, draft, _ = _setup()
    ctx = _with_deadline(ctx, 104, 600.0, buffer=0)
    assert set(dp.fit_until(ctx, stops, draft, 104)) == {'late', 'reordered', 'kept', 'split', 'hint'}
    view_ = dp.plan_view(ctx, stops, draft, _info, explain=False)
    assert all('window_tight' not in s and 'window_tight' not in tr for t in view_['trucks'] for tr in t['trips']
               for s in tr['stops'])


def test_build_rebuilds_store_blocked_only_by_buffer():
    """Сборка: с окном до срока − запас магазин не помещается (no_window), с настоящим сроком — помещается: сборка
    пересобирает его с настоящим сроком — он в рейсе, в плане жёлтый, не красный."""
    ctx, stops, _, _ = _setup()
    end = _first_eta(ctx, stops, 104) + 8
    hard = replace(ctx, windows={104: (-INF, end - 15)})          # то же окно, но без настоящего срока
    assert 104 in dp.build(hard, stops, None, [HOWO.car_code, FORD.car_code], 'now').no_window
    soft = _with_deadline(ctx, 104, end)
    draft = dp.build(soft, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    assert 104 not in draft.no_window and any(104 in t.stops for t in draft.trips)
    s = _stops_of(dp.plan_view(soft, stops, draft, _info, explain=False))[104][1]
    assert s['window_miss'] is False


def _added_day():
    """Рейс HOWO с магазинами запада; FORD без рейсов; новый магазин 105 к востоку (его ещё нет в рейсах)."""
    from test_route_dispatch_until import EAST, WEST, LAT, LON
    from test_route_optimizer import _dp_ctx, _dp_stops
    spec = [(c, p, 200.0) for c, p in WEST + EAST] + [(105, (LAT + 0.01, LON + 0.04), 150.0)]
    stops, orders = _dp_stops(spec)
    draft = dp.Draft(trucks=sorted([HOWO.car_code, FORD.car_code]), next_id=2,
                     trips=[dp.DraftTrip(1, HOWO.car_code, [c for c, _ in WEST])])
    return _dp_ctx(), stops, draft, orders


def test_place_added_falls_back_to_real_deadline():
    """«Տանել այսօր»: 105 с запасом не успеть нигде (срок = прибытие новым рейсом FORD + 5 мин, запас 15) — второй
    проход к настоящему сроку ставит его (жёлтое window_tight), а не оставляет «ещё не в рейсах»."""
    ctx, stops, draft, _ = _added_day()
    assert dp.place_added(ctx, stops, dp.Draft.from_json(draft.to_json()), [105])   # без срока — место есть
    routable = {s.customer_id: s for s in stops}
    first = 540 + dp._timeline(ctx, [dp.DraftTrip(9, FORD.car_code, [105])], routable, {105: 1})[9][2][0]
    hard = replace(ctx, windows={105: (-INF, first - 10)})
    assert dp.place_added(hard, stops, dp.Draft.from_json(draft.to_json()), [105]) == {}
    soft = _with_deadline(ctx, 105, first + 5)
    placed = dp.place_added(soft, stops, draft, [105])
    assert list(placed) == [105]
    s = _stops_of(dp.plan_view(soft, stops, draft, _info, explain=False))[105][1]
    assert s['window_miss'] is False and s['window_tight'] is True


def test_same_day_options_fall_back_to_real_deadline():
    """Новые заказы дня (№72): с запасом вариантов нет — варианты к настоящему сроку (и «Ընտրել» берёт тот же ключ)."""
    ctx, stops, draft, orders = _added_day()
    base = [s for s in stops if s.customer_id != 105]
    routable = {s.customer_id: s for s in stops}
    first = 540 + dp._timeline(ctx, [dp.DraftTrip(9, FORD.car_code, [105])], routable, {105: 1})[9][2][0]
    hard = replace(ctx, windows={105: (-INF, first - 10)})
    assert dp.same_day_options(hard, base, stops, draft, [105], -30.0)['options'] == []
    soft = _with_deadline(ctx, 105, first + 5)
    options = dp.same_day_options(soft, base, stops, draft, [105], -30.0)['options']
    assert options and all(_minutes(x['eta']) <= first + 5 for o in options for x in o['stops'])
    isn = next(o.isn for o in orders if o.customer_id == 105)
    taken = dp.take_same_day(soft, base, stops, draft, [105], {isn}, options[0]['key'], -30.0)
    assert any(105 in t.stops for t in taken.trips)


def test_dispatch_page_assets_tight_and_setting():
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'stop.window_tight' in js and 'քիչ ժամանակ կա՝ ' in js and 'u.tight' in js
    assert "routes_dispatch.js') }}?v=110" in (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    settings = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    assert "key: 'until_buffer_min', label: 'Ժամկետի պաշար, րոպե'" in settings
    assert "routes_settings.js') }}?v=42" in (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')


# ============================== /day: срок, рейсы и матрица ==============================

def _road(fail=False):
    """Дорожная модель: км — по прямой, минуты — 2 мин/км (30 км/ч); у магазина — 8 мин + 6 мин/т; fail — сбой участка."""
    def pair(a, b, minute):
        if fail:
            raise RuntimeError('нет дорог')
        km = haversine_km(a, b)
        return km, km * 2.0
    return live.Road(pair=pair, unload=lambda p, kg: 8.0 + 6.0 * kg / 1000.0)


def _demo(windows=None, plan=(), road=lambda customers, car: _road(), departs=None):
    data, base, loaded = dy.demo_data()
    view_ = replace(base, windows=windows or {}, until_buffer_min=15, trips=tuple(plan), plan_exists=bool(plan),
                    road=road, departs=departs or {})
    return data, view_, loaded


def test_day_until_fields_trips_and_matrix(tmp_path):
    from courier.store import Store
    store = Store(str(tmp_path / 'c.db'))
    data, view_, loaded = _demo(windows={900001: (-INF, 660.0), 900002: (600.0, 690.0), 900003: (720.0, INF)},
                                plan=[('TEST', (900002, 900001)), ('TEST', (900003,))],
                                departs={'TEST': (580.0, None)})
    body = dy.day_payload(data, view_, store, loaded)
    by_cid = {s['customer']['id']: s for s in body['stops']}
    assert (by_cid[900001]['until'], by_cid[900001]['until_from']) == ('11:00', None)
    assert (by_cid[900002]['until'], by_cid[900002]['until_from']) == ('11:30', '10:00')
    assert (by_cid[900003]['until'], by_cid[900003]['until_from']) == (None, '12:00')
    assert body['until_buffer_min'] == 15.0 and body['order_source'] == 'dispatch'
    t1, t2 = body['trips']
    assert (t1['trip'], t1['stop_ids']) == (1, [by_cid[900002]['stop_id'], by_cid[900001]['stop_id']])
    assert (t2['trip'], t2['stop_ids']) == (2, [by_cid[900003]['stop_id']])
    assert (t1['plan_version'], t2['plan_version']) == (ac.plan_version([900002, 900001]), ac.plan_version([900003]))
    assert len(t1['plan_version']) == 12 and t1['plan_version'] != ac.plan_version([900001, 900002])
    m = t1['matrix']
    assert m['nodes'] == ['depot', *t1['stop_ids']] and m['depart'] == '09:40' and m['source'] == 'road'
    pts = [(body['depot']['lat'], body['depot']['lon'])] + [(by_cid[c]['lat'], by_cid[c]['lon']) for c in (900002, 900001)]
    for i, a in enumerate(pts):
        for j, b in enumerate(pts):
            assert m['distances_m'][i][j] == round(haversine_km(a, b) * 1000)
            assert m['durations_s'][i][j] == round(haversine_km(a, b) * 2.0 * 60)
    assert m['service_s'] == [0, round((8 + 6 * by_cid[900002]['weight_kg'] / 1000) * 60),
                              round((8 + 6 * by_cid[900001]['weight_kg'] / 1000) * 60)]
    assert t2['matrix']['depart'] is None and len(t2['matrix']['nodes']) == 2
    # trips и until_buffer_min — не точки: version от них не зависит
    assert '"until_buffer_min": 15}' in json.dumps(body) and '"service_s": [0, ' in json.dumps(body)   # целые (H1)
    assert all(isinstance(x, int) for t in body['trips'] for m in [t['matrix']]
               for row in (*m['durations_s'], *m['distances_m'], m['service_s']) for x in row)
    dy.clear_matrix_cache()
    again = dy.day_payload(data, replace(view_, road=None, until_buffer_min=0), store, loaded)
    assert again['version'] == body['version'] and [t['matrix'] for t in again['trips']] == [None, None]


def test_day_trip_rules_without_plan_and_degrade(tmp_path):
    from courier.store import Store
    store = Store(str(tmp_path / 'c.db'))
    data, view_, loaded = _demo()                                     # плана нет — один рейс, все точки
    body = dy.day_payload(data, view_, store, loaded)
    assert [t['trip'] for t in body['trips']] == [1] and len(body['trips'][0]['stop_ids']) == 3
    assert body['trips'][0]['plan_version'] is None                    # плана нет — версии нет
    assert all(s['until'] is None and s['until_from'] is None for s in body['stops'])
    # сбой дорожной модели (сборка или участок) — matrix null, /day живой
    for road in (lambda pts, car: 1 / 0, lambda pts, car: _road(fail=True)):
        dy.clear_matrix_cache()
        got = dy.day_payload(data, replace(view_, road=road), store, loaded)
        assert got['trips'][0]['matrix'] is None and got['version'] == body['version']
    # точка вне плана — рейс 1; точек с координатой больше MATRIX_MAX_STOPS — без матрицы
    dy.clear_matrix_cache()
    data2, view2, _ = _demo(plan=[('TEST', (900003,)), ('TEST', (900001,))])
    trips = dy.day_payload(data2, view2, store, loaded)['trips']
    assert [(t['trip'], len(t['stop_ids'])) for t in trips] == [(1, 2), (2, 1)]
    stops = [{'stop_id': f'S:{i}', 'customer': {'id': i}, 'lat': 40.1 + i / 1000, 'lon': 44.5, 'weight_kg': 1.0}
             for i in range(dy.MATRIX_MAX_STOPS + 1)]
    assert dy._matrix(stops, (40.18, 44.51), _road(), None, 540.0) is None
    assert dy._matrix(stops[:-1], (40.18, 44.51), _road(), None, 540.0) is not None


def test_routes_view_carries_until_and_trip_road(tmp_path, monkeypatch):
    """Вид «Маршрутов» для /day: окна дня, запас, начало дня машины; дорожная модель — route_optimizer.views.trip_road."""
    from flask import Flask
    import route_optimizer
    from route_optimizer import views

    class FakeDb:
        connection_string = 'DRIVER={none};'
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))
    flask_app = Flask(__name__)
    flask_app.secret_key = 'test'
    route_optimizer.init_app(flask_app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    state = flask_app.extensions['route_optimizer']
    state.store.save_customer_window(11, rst.CustomerWindow('before', 660), 'qa')
    view_ = rl.routes_view(state, date(2026, 10, 1))
    assert view_.windows == {11: (-INF, 660.0)} and view_.until_buffer_min == 15.0 and view_.work_start_min == 540.0
    road = view_.road({11: (40.17, 44.47)}, 'CAR1')
    assert isinstance(road, live.Road) and road.unload is not None
    km, minutes, by_road = road.drive((40.15, 44.45), (40.17, 44.47), 600.0)
    assert km > 0 and minutes > 0 and by_road is False                 # карты нет — запасная модель
    assert views.trip_road(state, date(2026, 10, 1), {11: (40.17, 44.47)}).unload((40.17, 44.47), 1000.0) > 0


def test_departs_from_prediction():
    draft = dp.Draft(trips=[dp.DraftTrip(1, 'A', [1]), dp.DraftTrip(2, 'A', [2]), dp.DraftTrip(3, 'B', [3])])
    draft.prediction = {'trucks': {'A': {'trips': [{'depart': '09:05'}, {'depart': None}]},
                                   'B': {'trips': [{'depart': '10:00'}, {'depart': '13:00'}]}}}
    assert rl._departs(draft) == {'A': (545, None), 'B': (None,)}   # у B в прогнозе другое число рейсов


# ============================== событие reorder ==============================

@pytest.fixture
def term_until(st, client, monkeypatch):
    """Демо-день, где у магазина 900001 срок 11:00 (у остальных нет)."""
    data, base, loaded = dy.demo_data()
    monkeypatch.setattr(dy, 'demo_data', lambda: (data, replace(base, windows={900001: (-INF, 660.0)}), loaded))
    _, _, h = make_terminal(st)
    s = login(client, h)
    body = client.get(f'/api/courier/v1/day?date={DEMO}', headers=s).get_json()
    by_cid = {x['customer']['id']: x['stop_id'] for x in body['stops']}
    return s, body, by_cid


def _reorder(order, reason='until', trip=1, moved=None, at='2000-01-01T10:05:00+04:00', **kw):
    return event('reorder', None, {'trip': trip, 'moved': order[0] if moved is None and order else moved,
                                   'order': order, 'reason': reason}, at=at, **kw)


def test_reorder_accepted_flags_and_idempotent(term_until, app, client, st):
    s, body, cid = term_until
    assert body['stops'][[x['customer']['id'] for x in body['stops']].index(900001)]['until'] == '11:00'
    ok = _reorder([cid[900001], cid[900002], cid[900003]])
    ok['payload']['plan_version'] = 'abc123def456'
    no_deadline = _reorder([cid[900002], cid[900003]], at='2000-01-01T10:20:00+04:00')
    driver = _reorder([cid[900003], cid[900002]].copy(), 'driver', at='2000-01-01T10:30:00+04:00')
    got = post(client, s, ok, no_deadline, driver)
    assert len(got['accepted']) == 3 and not got['rejected']
    assert post(client, s, ok)['duplicates'] == [ok['id']]                 # тот же id — повтор, не новое событие
    rows = {e['id']: e for e in st.store.events_for_day(DEMO, 'reorder')}
    assert rows[ok['id']]['payload'] == {'trip': 1, 'order': ok['payload']['order'], 'moved': cid[900001],
                                         'reason': 'until', 'plan_version': 'abc123def456'} and rows[ok['id']]['flags'] == []
    assert 'plan_version' not in rows[driver['id']]['payload']
    assert rows[no_deadline['id']]['payload']['reason'] == 'driver'        # срока нет — не «срок под риском»
    assert rows[no_deadline['id']]['payload']['reason_sent'] == 'until' and rows[no_deadline['id']]['flags'] == ['no_deadline']
    assert rows[driver['id']]['payload']['reason'] == 'driver' and rows[driver['id']]['flags'] == []
    moves = st.store.reorders(DEMO)
    assert [(m['car_code'], m['moved'], m['reason']) for m in moves] == [
        ('TEST', cid[900001], 'until'), ('TEST', cid[900002], 'driver'), ('TEST', cid[900003], 'driver')]
    assert moves[0]['at'] == '2000-01-01T10:05:00+04:00' and moves[0]['trip'] == 1
    assert (moves[0]['plan_version'], moves[1]['plan_version']) == ('abc123def456', None)
    assert st.store.reorders(DEMO, 'OTHER') == [] and FactsSource(st.store).reorders('TEST', DEMO) == moves
    with app.app_context():
        fleet = LiveSource(st.store).fleet(DEMO)
    assert [m['id'] for m in fleet['TEST']['reorders']] == [m['id'] for m in moves]


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(trip=0), lambda p: p.update(trip=51), lambda p: p.update(trip=True), lambda p: p.update(trip='1'),
    lambda p: p.update(order=[]), lambda p: p.update(order='S:1'), lambda p: p.update(order=['S:bad']),
    lambda p: p.update(order=p['order'] + [p['order'][0]]), lambda p: p.update(order=p['order'] * 101),
    lambda p: p.update(moved=p['order'][1]), lambda p: p.pop('moved'), lambda p: p.update(reason='late'),
    lambda p: p.pop('reason'), lambda p: p.update(plan_version=12), lambda p: p.update(plan_version='x' * 65)])
def test_reorder_rejections(term_until, client, mutate):
    s, _, cid = term_until
    e = _reorder([cid[900001], cid[900002]])
    mutate(e['payload'])
    got = post(client, s, e)
    assert [r['id'] for r in got['rejected']] == [e['id']], got


def test_reorder_moved_case_insensitive(term_until, client, st):
    s, _, cid = term_until
    order = [cid[900001].lower().replace('s:', 'S:'), cid[900002]]
    assert post(client, s, _reorder(order, moved=order[0]))['accepted']
    assert st.store.reorders(DEMO)[0]['moved'] == cid[900001]


# ============================== эталон порядка: actuals ==============================

CUST = {'S0': 10, 'S1': 11, 'S2': 12, 'S3': 13, 'SX': None}


def _r(minute, order, reason, trip=0):
    cs = tuple(CUST[s] for s in order)
    return ac.Reorder(T(minute), trip, cs, cs[0], order[0], reason)


def test_reorders_of_parses_and_sorts():
    raw = [{'at': T(30).isoformat(), 'trip': 1, 'order': ['S3', 'S1', 'SX', 'S1'], 'moved': 'S3', 'reason': 'driver'},
           {'at': T(10).isoformat(), 'trip': 2, 'order': ['S2'], 'moved': 'S2', 'reason': 'until'},
           {'at': '2026-10-05T09:00:00', 'trip': 1, 'order': ['S1'], 'moved': 'S1', 'reason': 'until'},   # без зоны
           {'at': T(5).isoformat(), 'trip': 0, 'order': ['S1'], 'moved': 'S1', 'reason': 'until'},
           {'at': T(5).isoformat(), 'trip': 1, 'order': ['S1'], 'moved': 'S1', 'reason': 'late'},
           {'at': T(5).isoformat(), 'trip': 1, 'order': ['S1', 'S2'], 'moved': 'S2', 'reason': 'until'},
           {'at': T(5).isoformat(), 'trip': 1, 'order': ['SX'], 'moved': 'SX', 'reason': 'until'},
           {'at': 'x', 'trip': 1, 'order': ['S1'], 'moved': 'S1', 'reason': 'until'}]
    got = ac.reorders_of(raw, CUST)
    assert got == [ac.Reorder(T(10), 1, (12,), 12, 'S2', 'until'), ac.Reorder(T(30), 0, (13, 11), 13, 'S3', 'driver')]


def test_reorder_trip_keeps_served_first_and_unknown_last():
    r = ac.Reorder(T(0), 0, (13, 11, 99), 13, 'S3', 'until')
    assert ac.reorder_trip([10, 11, 12, 13], r, {10}) == [10, 13, 11, 12]       # 12 терминал не переставлял — в конце
    assert ac.reorder_trip([10, 11, 12, 13], r, {10, 12}) == [10, 12, 13, 11]
    assert ac.reorder_trip([10, 11], ac.Reorder(T(0), 0, (99,), 99, 'S9', 'until'), set()) == [10, 11]


def test_current_reorders_drop_changes_on_previous_plan_version():
    """Логист пересобрал рейс и отправил водителям после смены водителя — смена на прежней версии рейса не эталон; без
    версии (старый APK) — эталон."""
    old, new = [10, 11, 12, 13], [10, 12, 11, 13]
    on_old = replace(_r(15, ['S3', 'S1'], 'driver'), plan_version=ac.plan_version(old))
    no_version = _r(16, ['S3', 'S1'], 'driver')
    other_trip = replace(_r(17, ['S3'], 'driver', trip=5), plan_version=ac.plan_version(old))
    assert ac.current_reorders([on_old, no_version, other_trip], [old]) == [on_old, no_version]
    assert ac.current_reorders([on_old, no_version], [new]) == [no_version]
    raw = [{'at': T(15).isoformat(), 'trip': 1, 'order': ['S3', 'S1'], 'moved': 'S3', 'reason': 'until',
            'plan_version': ac.plan_version(old)}]
    assert ac.reorders_of(raw, CUST)[0].plan_version == ac.plan_version(old)


def _actual(served):
    """Факт дня: обслуживающие визиты точек по порядку (точка, минута прибытия)."""
    visits = tuple(ac.Visit((k,), T(m), T(m + 5), 0, False) for k, m in served)
    return ac.DayActual(0, 0.0, None, None, visits=visits, served=tuple((k, i) for i, (k, _) in enumerate(served)))


PSTOPS = [ac.PlanStop(f'S{i}', 10 + i, (40.0, 44.0), 100.0, rank=i) for i in range(4)]
PLAN = [[10, 11, 12, 13]]


def test_reordered_changes_matches_today_without_reorders():
    for served in ([('S0', 10), ('S1', 20), ('S2', 30), ('S3', 40)], [('S0', 10), ('S3', 20), ('S1', 30), ('S2', 40)],
                   [('S2', 10), ('S0', 20)]):
        vm = ac.visit_metrics(_actual(served), PSTOPS, LDAY)
        assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, []) == (vm.order_changes, vm.ordered)


def test_reordered_changes_until_free_driver_counts_jump():
    """S0, затем «Գնալ առաջինը» S3 (09:15), затем S3, S1, S2. Сегодня — 1 точка не по порядку; 'until' — 0; 'driver' — 1
    (только перенесённая, новый порядок остальных — без штрафа)."""
    served = [('S0', 10), ('S3', 20), ('S1', 30), ('S2', 40)]
    assert ac.visit_metrics(_actual(served), PSTOPS, LDAY).order_changes == 1
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, [_r(15, ['S3', 'S1', 'S2'], 'until')]) == (0, 4)
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, [_r(15, ['S3', 'S1', 'S2'], 'driver')]) == (1, 4)
    # смена до первого магазина: обслуженных ещё нет — пропусков до неё нет
    first = [('S1', 10), ('S0', 20), ('S2', 30), ('S3', 40)]
    assert ac.reordered_changes(_actual(first), PSTOPS, PLAN, [_r(5, ['S1', 'S0', 'S2', 'S3'], 'until')]) == (0, 4)
    # «Գնալ առաջինը» у следующей по плану точки — не прыжок
    assert ac.reordered_changes(_actual([('S0', 10), ('S1', 20), ('S2', 30), ('S3', 40)]), PSTOPS, PLAN,
                                [_r(15, ['S1', 'S3', 'S2'], 'driver')]) == (1, 4)   # S3 раньше S2 — по новому порядку
    assert ac.reordered_changes(_actual([('S0', 10), ('S1', 20), ('S3', 30), ('S2', 40)]), PSTOPS, PLAN,
                                [_r(15, ['S1', 'S3', 'S2'], 'driver')]) == (0, 4)


def test_reordered_changes_skip_before_reorder_is_not_forgiven():
    """S2 обслужена раньше S1 (пропуск), потом смена 'until' (S3, S1): новый эталон S1 не держит, но пропуск до смены
    остаётся нарушением — как сегодня (1)."""
    served = [('S0', 10), ('S2', 20), ('S3', 30), ('S1', 40)]
    today = ac.visit_metrics(_actual(served), PSTOPS, LDAY).order_changes
    assert today == 1
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, [_r(25, ['S3', 'S1'], 'until')]) == (1, 4)


# ============================== карта машин: порядок объезда ==============================

NOS = {10: 1, 11: 2, 12: 3, 13: 4}


def _seq_stops(statuses):
    pts = [A, B, C, D]
    return [stop(f'S{i}', 10 + i, pts[i], 100.0, s, seq=i + 1) for i, s in enumerate(statuses)]


def test_sequence_until_no_alarm():
    plan = [live.PlanTrip((10, 11, 12, 13), {})]
    stops = _seq_stops(['full', 'pending', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    touches = {'S0': T(10), 'S3': T(30)}
    before = live.sequence_check(stops, trips, plan, touches, NOS, True)
    assert before[0][0]['active'] is True and [x['stop_id'] for x in before[1]['skipped']] == ['S1', 'S2']
    moves = [_r(15, ['S3', 'S1', 'S2'], 'until')]
    assert live.sequence_check(stops, trips, plan, touches, NOS, True, moves) == ([], {'skipped': [], 'pairs': []})
    done = {**touches, 'S1': T(40), 'S2': T(50)}
    assert live.sequence_check(_seq_stops(['full'] * 4), trips, plan, done, NOS, True, moves) == \
        ([], {'skipped': [], 'pairs': []})


def test_sequence_driver_alarm_like_today_then_new_order():
    plan = [live.PlanTrip((10, 11, 12, 13), {})]
    stops = _seq_stops(['full', 'pending', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    moves = [_r(15, ['S3', 'S1', 'S2'], 'driver')]
    alerts, summary = live.sequence_check(stops, trips, plan, {'S0': T(10), 'S3': T(30)}, NOS, True, moves)
    assert len(alerts) == 1 and (alerts[0]['active'], alerts[0]['from']) == (True, T(30).isoformat())
    assert alerts[0]['jump']['stop_id'] == 'S3' and [x['stop_id'] for x in summary['skipped']] == ['S1', 'S2']
    # долг закрыт по новому порядку — эпизод кончился; пара «№4 раньше №2», как сегодня
    done = {'S0': T(10), 'S3': T(30), 'S1': T(40), 'S2': T(50)}
    alerts, summary = live.sequence_check(_seq_stops(['full'] * 4), trips, plan, done, NOS, True, moves)
    assert (alerts[0]['active'], alerts[0]['to']) == (False, T(50).isoformat()) and summary['skipped'] == []
    assert [(p['first']['no'], p['then']['no']) for p in summary['pairs']] == [(4, 2)]
    assert live.sequence_check(_seq_stops(['full'] * 4), trips, plan, done, NOS, True)[1]['pairs'] == summary['pairs']
    # перенос следующей по плану точки — не прыжок: ни тревоги, ни пар
    moves = [_r(15, ['S1', 'S3', 'S2'], 'driver')]
    done = {'S0': T(10), 'S1': T(20), 'S3': T(30), 'S2': T(40)}
    assert live.sequence_check(_seq_stops(['full'] * 4), trips, plan, done, NOS, True, moves) == \
        ([], {'skipped': [], 'pairs': []})


def test_sequence_until_after_skip_ends_episode_at_reorder():
    plan = [live.PlanTrip((10, 11, 12, 13), {})]
    stops = _seq_stops(['full', 'pending', 'full', 'pending'])
    trips = live.trip_of(stops, plan)
    touches = {'S0': T(10), 'S2': T(20)}
    alerts, _ = live.sequence_check(stops, trips, plan, touches, NOS, True, [_r(25, ['S3', 'S1'], 'until')])
    assert len(alerts) == 1 and (alerts[0]['active'], alerts[0]['to']) == (False, T(25).isoformat())
    assert alerts[0]['skipped'] == [{'stop_id': 'S1', 'name': 'Խանութ 11', 'no': 2, 'open': False}]


def test_reordered_plan_slots_etas():
    plan = [live.PlanTrip((10, 11, 12, 13), {10: T(10), 11: T(20), 12: T(30), 13: T(40)}, T(0)),
            live.PlanTrip((20,), {20: T(90)})]
    stops = _seq_stops(['full', 'pending', 'pending', 'pending'])
    got = live.reordered_plan(plan, [_r(15, ['S3', 'S1', 'S2'], 'until')], stops, {'S0': T(10)})
    assert got[0].customers == (10, 13, 11, 12) and got[0].depart == T(0)
    assert got[0].etas == {10: T(10), 13: T(20), 11: T(30), 12: T(40)} and got[1] is plan[1]
    assert live.reordered_plan(plan, [], stops, {}) == plan


def test_car_view_reorder_until_next_eta_and_card():
    """Склад → S0 (закрыта) → «Գնալ առաջինը» S3 со сроком: следующий — S3, его плановое ETA — слот S1, тревоги порядка
    нет, в карточке — reorders (страница: фиолетовое сведение)."""
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 6).drive(B)
    now = tr.t
    stops = _seq_stops(['full', 'pending', 'pending', 'pending'])
    stops[0]['delivered_at'] = (at_a + timedelta(minutes=3)).isoformat()
    plan = [live.PlanTrip((10, 11, 12, 13), {10: at_a, 11: at_a + timedelta(minutes=20),
                                             12: at_a + timedelta(minutes=40), 13: at_a + timedelta(minutes=60)}, T(10))]
    f = facts(tr.pts, stops, [T0, now])
    f['reorders'] = [{'at': (at_a + timedelta(minutes=7)).isoformat(), 'trip': 1, 'order': ['S3', 'S1', 'S2'],
                      'moved': 'S3', 'reason': 'until'}]
    card = view(f, now, plan, detail=True)
    assert card['next']['stop_id'] == 'S3'
    assert card['next']['planned_eta'] == (at_a + timedelta(minutes=20)).isoformat(timespec='seconds')
    assert card['reorders'] == [{'trip': 1, 'at': (at_a + timedelta(minutes=7)).isoformat(timespec='seconds'),
                                 'reason': 'until', 'moved': {'stop_id': 'S3', 'name': 'Խանութ 13', 'no': None}}]
    assert 'sequence' not in card['alerts']['active']
    etas = {s['stop_id']: s['planned_eta'] for s in card['stops']}
    assert etas['S1'] == (at_a + timedelta(minutes=40)).isoformat(timespec='seconds')
    stale = dict(f, reorders=[{**f['reorders'][0], 'plan_version': ac.plan_version([10, 12, 11, 13])}])
    assert view(stale, now, plan)['next']['stop_id'] == 'S1'          # смена на прежней версии рейса — не эталон
    without = view(facts(tr.pts, stops, [T0, now]), now, plan)
    assert without['next']['stop_id'] == 'S1' and without['reorders'] == []


def test_live_page_violet_info_for_until():
    js = (ROOT / 'static' / 'js' / 'routes_live.js').read_text(encoding='utf-8')
    assert "REORDER_TITLE = 'Վարորդը փոխեց հերթը՝ ժամկետի պատճառով'" in js
    assert "out.push(one('reorder', 3, REORDER_TITLE" in js and "r.reason === 'until'" in js
    assert "routes_live.js') }}?v=23" in (ROOT / 'templates' / 'routes_live.html').read_text(encoding='utf-8')


# ============================== «Վարորդներ»: порядок со сменами ==============================

def test_scorecard_on_time_uses_reslotted_etas():
    """«Ժամանակին»: план 10 → 11 → 12 (09:10, 09:30, 09:50); в 09:05 водитель ставит 12 первым (срок) и объезжает 12,
    10, 11 вовремя по рейсу. Без смены 10 и 11 — «позже плана» на 22 мин; с ней — плановые моменты рейса по новому
    порядку, все вовремя. Опоздание к окну приёма (настоящий срок) остаётся опозданием."""
    from route_optimizer import scorecard as sc
    from route_optimizer import views
    at = lambda h, m: datetime(2026, 10, 5, h, m, tzinfo=ac.YEREVAN)   # noqa: E731
    draft = {'trips': [{'truck': 'CAR1', 'stops': [10, 11, 12]}],
             'prediction': {'trucks': {'CAR1': {'trips': [{'depart': '09:00', 'stops': [[10, '09:10'], [11, '09:30'],
                                                                                         [12, '09:50']]}]}}}}
    stops = [ac.PlanStop(f'S{i}', 10 + i, (40.0, 44.0), 100.0, rank=i) for i in range(3)]
    visits = (ac.Visit(('S2',), at(9, 12), at(9, 17), 0, False), ac.Visit(('S0',), at(9, 32), at(9, 37), 0, False),
              ac.Visit(('S1',), at(9, 52), at(9, 57), 0, False))
    actual = ac.DayActual(0, 0.0, None, None, visits=visits, served=(('S2', 0), ('S0', 1), ('S1', 2)))
    move = ac.Reorder(at(9, 5), 0, (12, 10, 11), 12, 'S2', 'until')
    plain = views._stop_etas(draft, 'CAR1', LDAY, stops, actual)
    moved = views._stop_etas(draft, 'CAR1', LDAY, stops, actual, [move])
    assert {k: v.strftime('%H:%M') for k, v in moved.items()} == {'S2': '09:10', 'S0': '09:30', 'S1': '09:50'}
    marks = {k: {'arrive': actual.visits[i].arrive, 'late_min': 0.0, 'early': False, 'window': False}
             for k, i in actual.served}
    assert [sc.timing(marks[k], plain[k])[0] for k in ('S2', 'S0', 'S1')] == [True, False, False]
    assert [sc.timing(marks[k], moved[k])[0] for k in ('S2', 'S0', 'S1')] == [True, True, True]
    late = {**marks['S0'], 'window': True, 'late_min': 7.0}   # мимо настоящего срока магазина — опоздание и со сменой
    assert sc.timing(late, moved['S0']) == (False, 7.0, 'window')
    assert ac.reslot_etas(actual, stops, [[10, 11, 12]], [], [{10: at(9, 10)}]) == [{10: at(9, 10)}]


def test_scorecard_order_uses_reorders(sc_app):
    """CAR1: план A, B; обслужены A, B. Смена 'until' «B, A» до A: эталон B, A — A вне порядка (50 %); 'driver' — ещё
    штраф за прыжок B (0 %); источник без reorders — как раньше (100 %)."""
    from test_garage_public import LAN, _session_as
    from test_route_driver_scorecard import D1, D2, T as ST
    app_v2, _, _ = sc_app
    state = app_v2.app.extensions['route_optimizer']
    web = app_v2.app.test_client()
    fleet = state.fleet_facts

    def pct(reason):
        state.scorecard_cache.clear()
        fleet.reorders = (lambda car, ds: []) if reason is None else (
            lambda car, ds: [{'at': ST(9, 5).isoformat(), 'trip': 1, 'order': ['S:B', 'S:A'], 'moved': 'S:B',
                              'reason': reason}] if (car, ds) == ('CAR1', D1) else [])
        _session_as(web, 'boss', base=LAN)
        body = web.get(f'/api/routes/drivers/scorecard?from={D1}&to={D2}', base_url=LAN).get_json()
        return next(x for x in body['drivers'] if x['key'] == 'driver:1')['order_pct']
    assert (pct(None), pct('until'), pct('driver')) == (100.0, 50.0, 0.0)


from test_garage_public import app_v2  # noqa: E402,F401
from test_route_driver_scorecard import sc_app  # noqa: E402,F401


# ============================== матрица рейса = ETA плана (№66 темп, №68 Ереван) ==============================

from test_route_optimizer import DP_DAY, DP_DEPOT, _dispatch_setup, _dorder, _no_road_map  # noqa: E402,F401
from test_route_optimizer import client as rclient  # noqa: E402,F401
from test_route_big_truck import CITY, OUTSIDE  # noqa: E402
from test_route_store_unload import _build  # noqa: E402


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def test_matrix_eta_matches_plan_eta(rclient, monkeypatch):   # noqa: F811
    """Телефон считает прибытия по матрице рейса (выезд + участки + время у точек) — для неизменённого порядка они те же,
    что ETA плана «Развоза» (±1 мин округления HH:MM), с темпом машины (№66) и надбавкой большой машины в Ереване (№68)."""
    from route_optimizer import views
    _dispatch_setup(rclient, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0), _dorder(3, 104, 900.0)])
    assert rclient.post('/api/routes/settings', json={'settings': {
        'yerevan_zone': rst.DEFAULT_SETTINGS['yerevan_zone']}}).status_code == 200
    state = rclient.application.extensions['route_optimizer']
    for cid, p in ((101, CITY[0]), (102, OUTSIDE[0]), (104, CITY[2])):
        state.store.save_geo_override(cid, p, 'qa')
    learned = views._with_learned   # темп CAR1 — как выученный (№66): у плана и у матрицы одно чтение норм

    def paced(*a, **kw):
        norms, tn, trucks, eff = learned(*a, **kw)
        return norms, replace(tn, pace={'CAR1': (1.2, 1.1)}), trucks, eff
    monkeypatch.setattr(views, '_with_learned', paced)
    plan = _build(rclient, ('CAR1',))['plan']
    trips = [tr for t in plan['trucks'] for tr in t['trips']]
    assert trips and any('yerevan_min' in s for tr in trips for s in tr['stops'])
    for trip in trips:
        xs = [{'stop_id': f'S:{s["customer_id"]}', 'customer': {'id': s['customer_id']}, 'lat': s['lat'], 'lon': s['lon'],
               'weight_kg': float(s['kg'])} for s in trip['stops']]
        road = views.trip_road(state, DP_DAY, {x['customer']['id']: (x['lat'], x['lon']) for x in xs}, 'CAR1')
        m = dy._matrix(xs, DP_DEPOT, road, float(_minutes(trip['depart'])), 540.0)
        t, prev = float(_minutes(trip['depart'])), 0
        for i, s in enumerate(trip['stops'], 1):
            t += m['durations_s'][prev][i] / 60.0
            assert abs(t - _minutes(s['eta'])) <= 1.0, (s['customer_id'], t, s['eta'])
            t += m['service_s'][i] / 60.0
            prev = i
    # без машины (ETA карты машин) — без темпа и надбавки: матрица другая
    plain = views.trip_road(state, DP_DAY, {101: CITY[0]})
    assert plain.unload(CITY[0], 400.0) < views.trip_road(state, DP_DAY, {101: CITY[0]}, 'CAR1').unload(CITY[0], 400.0)


# ============================== плановая линия после смены порядка ==============================

from test_route_live import MID_AB, _off, _route, _route_view  # noqa: E402


def test_stale_plan_line_after_reorder_suppresses_deviation():
    """Линия по новому порядку ещё строится (stale_since): отклонение после смены — не тревога; до смены — как было."""
    side = _off(MID_AB, 1500.0)
    tr = Track().park(DEPOT, 5).drive(A).park(A, 3)
    moved = tr.t
    tr.drive(side)
    assert _route_view(tr, tr.t + timedelta(seconds=20), _route(), detail=False)['deviation']['count'] == 1
    stale = replace(_route(), stale_since=moved)
    card = _route_view(tr, tr.t + timedelta(seconds=20), stale, detail=False)
    assert card['deviation']['count'] == 0 and 'deviation' not in card['alerts']['active']
    early = replace(_route(), stale_since=moved + timedelta(hours=1))      # отклонение началось раньше смены
    assert _route_view(tr, tr.t + timedelta(seconds=20), early, detail=False)['deviation']['count'] == 1


def test_plan_lines_follow_driver_order(tmp_path, monkeypatch):
    """Плановая линия рейса — по порядку водителя после «Գնալ առաջինը» (номера магазинов — по плану); смена на прежней
    версии рейса — линия по плану. Карты нет — линии по прямой (source = они же): stale_since нет."""
    from flask import Flask
    import route_optimizer
    from route_optimizer import views

    class FakeDb:
        connection_string = 'DRIVER={none};'
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))
    flask_app = Flask(__name__)
    flask_app.secret_key = 'test'
    route_optimizer.init_app(flask_app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    state = flask_app.extensions['route_optimizer']
    bundle = replace(state.store.load(), depot=DEPOT)
    draft = dp.Draft(trucks=['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [10, 11, 12, 13])])
    stops = _seq_stops(['full', 'pending', 'pending', 'pending'])
    stops[0]['delivered_at'] = T(10).isoformat()
    move = {'at': T(15).isoformat(), 'trip': 1, 'order': ['S3', 'S1', 'S2'], 'moved': 'S3', 'reason': 'until'}
    route = views._live_plan_routes(state, bundle, None, LDAY, draft, {'CAR1': {'stops': stops, 'reorders': [move]}})['CAR1']
    assert route.geo.trips[0] == (DEPOT, A, D, B, C, DEPOT) and route.trip_stops == ((10, 13, 11, 12),)
    assert [c for c, _ in route.stops] == [10, 11, 12, 13] and route.stale_since is None
    old = {**move, 'plan_version': ac.plan_version([10, 12, 11, 13])}
    route = views._live_plan_routes(state, bundle, None, LDAY, draft, {'CAR1': {'stops': stops, 'reorders': [old]}})['CAR1']
    assert route.geo.trips[0] == (DEPOT, A, B, C, D, DEPOT)
    # линии по дорогам ещё по старому порядку (кэш отдаёт прежние) — stale_since с момента смены
    plan_geo = live.RouteGeometry([(DEPOT, A, B, C, D, DEPOT)], [(DEPOT, A, B, C, D, DEPOT)], (), [(DEPOT, A, B, C, D, DEPOT)])
    monkeypatch.setattr(state, 'roads', type('R', (), {'get': lambda self: type('G', (), {'failed': False, 'version': 1})(),
                                                       'bypass': lambda self, r, z: r})())
    monkeypatch.setattr(state.live_lines, 'get', lambda day, wanted, build: {'CAR1': plan_geo})
    route = views._live_plan_routes(state, bundle, None, LDAY, draft, {'CAR1': {'stops': stops, 'reorders': [move]}})['CAR1']
    assert route.geo is plan_geo and route.stale_since == T(15)


# ============================== ревью: целые поля, 24:00, обслуженные в порядке водителя, прежние версии ==============================

def test_until_buffer_integer_and_end_of_day():
    base = dict(rst.DEFAULT_SETTINGS)
    assert 'until_buffer_min' in rst.validate_settings({**base, 'until_buffer_min': 15.5}, None)[1]
    assert rst.validate_settings({**base, 'until_buffer_min': 20.0}, None)[0]['until_buffer_min'] == 20
    assert (dy._hm(1440.0), dy._hm(1439.6), dy._hm(1500.0), dy._hm(0.0)) == ('24:00', '24:00', '24:00', '00:00')
    assert dy._hm(-90.0) == '00:00'                                              # окно «с 23:30 ± 2 ч» — не «-1:30»


def test_day_json_until_buffer_is_integer(term_until, client):
    s, _, _ = term_until
    raw = client.get(f'/api/courier/v1/day?date={DEMO}', headers=s).get_data(as_text=True).replace(' ', '')
    assert '"until_buffer_min":0' in raw and '"until_buffer_min":0.0' not in raw


def test_served_stop_inside_driver_order_keeps_its_place():
    """S0 обслужена, S1 — визит по GPS в 09:20, но в APK ещё открыта; в 09:25 «Գնալ առաջինը» у S3 (срок): терминал
    прислал S3, S1, S2. Обслуженная S1 — впереди эталона: ни штрафа «порядок», ни тревоги, ни пары."""
    assert ac.reorder_trip([10, 11, 12, 13], _r(25, ['S3', 'S1', 'S2'], 'until'), {10, 11}) == [10, 11, 13, 12]
    served = [('S0', 10), ('S1', 20), ('S3', 30), ('S2', 40)]
    move = _r(25, ['S3', 'S1', 'S2'], 'until')
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, [move]) == (0, 4)
    plan = [live.PlanTrip((10, 11, 12, 13), {10: T(10), 11: T(20), 12: T(30), 13: T(40)})]
    stops = _seq_stops(['full', 'pending', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    touches = {'S0': T(10), 'S1': T(20), 'S3': T(30)}
    assert live.sequence_check(stops, trips, plan, touches, NOS, True, [move]) == ([], {'skipped': [], 'pairs': []})
    done = {**touches, 'S2': T(40)}
    assert live.sequence_check(_seq_stops(['full'] * 4), trips, plan, done, NOS, True, [move]) == \
        ([], {'skipped': [], 'pairs': []})
    got = live.reordered_plan(plan, [move], stops, touches)[0]
    assert got.customers == (10, 11, 13, 12) and got.etas == {10: T(10), 11: T(20), 13: T(30), 12: T(40)}


def test_stale_until_move_stays_exempt_until_plan_resent():
    """В 09:15 «until»-смена S3 на прежней версии рейса; логист пересобрал рейс и отправил в 10:00. Уже сделанное до
    10:00 — по смене водителя (ни тревоги, ни штрафа), с 10:00 — эталон снова план; вперёд смена не действует."""
    old = [10, 11, 12, 13]
    stale = replace(_r(15, ['S3', 'S1', 'S2'], 'until'), plan_version=ac.plan_version([10, 12, 11, 13]))
    assert ac.current_reorders([stale], [old]) == []
    hist = ac.history_reorders([stale], [old], T(60))
    # план снова эталон, когда телефон его получил: не позже отправки + опрос /day (RESYNC = 15 мин + кэш сервера)
    assert hist[0] == stale and (hist[1].at, hist[1].reason, hist[1].customers) == (T(76), 'plan', tuple(old))
    fresh = replace(_r(65, ['S2', 'S1'], 'until'), plan_version=ac.plan_version(old))   # смена уже на новой версии
    assert [(x.at, x.reason) for x in ac.history_reorders([stale, fresh], [old], T(60))] == [
        (T(15), 'until'), (T(65), 'plan'), (T(65), 'until')]
    assert ac.history_reorders([stale], [old], None) == [stale]                 # отправки не знаем — без возврата к плану
    plan = [live.PlanTrip(tuple(old), {})]
    stops = _seq_stops(['full', 'pending', 'pending', 'full'])
    trips = live.trip_of(stops, plan)
    touches = {'S0': T(10), 'S3': T(30)}
    assert live.sequence_check(stops, trips, plan, touches, NOS, True, hist) == ([], {'skipped': [], 'pairs': []})
    served = [('S0', 10), ('S3', 30), ('S1', 70), ('S2', 80)]
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, hist) == (0, 4)
    assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, []) == (1, 4)   # без истории — штраф задним числом


def test_car_view_stale_move_before_resend_no_alarm():
    tr = Track().park(DEPOT, 10).drive(A)
    at_a = tr.t
    tr.park(A, 6).drive(D)
    at_d = tr.t
    tr.park(D, 6)
    now = tr.t
    stops = _seq_stops(['full', 'pending', 'pending', 'full'])
    stops[0]['delivered_at'] = (at_a + timedelta(minutes=3)).isoformat()
    stops[3]['delivered_at'] = (at_d + timedelta(minutes=3)).isoformat()
    plan = [live.PlanTrip((10, 11, 12, 13), {}, T(10))]
    f = facts(tr.pts, stops, [T0, now])
    f['reorders'] = [{'at': (at_a + timedelta(minutes=7)).isoformat(), 'trip': 1, 'order': ['S3', 'S1', 'S2'],
                      'moved': 'S3', 'reason': 'until', 'plan_version': ac.plan_version([10, 12, 11, 13])}]
    resend = now + timedelta(minutes=1)
    card = live.car_view(LDAY, now, f, plan, live.TruckSpec(), DEPOT, live.Rules(), live.Road(), False, sent_at=resend)
    assert 'sequence' not in card['alerts']['active'] and card['sequence']['skipped'] == []
    assert card['reorders'][0]['reason'] == 'until'                            # в журнале смен — и прежняя версия
    bare = live.car_view(LDAY, now, f, plan, live.TruckSpec(), DEPOT, live.Rules(), live.Road(), False)
    assert 'sequence' not in bare['alerts']['active']                          # отправки не знаем — смена в силе
    # логист отправил новый рейс сразу после смены: телефон его ещё не получил (до RESYNC) — S3 по смене водителя
    early = live.car_view(LDAY, now, f, plan, live.TruckSpec(), DEPOT, live.Rules(), live.Road(), False,
                          sent_at=at_a + timedelta(minutes=8))
    assert 'sequence' not in early['alerts']['active']
    # отправлен раньше смены: к приезду в S3 телефон уже получил новый план (отправка + RESYNC) — эталон снова план:
    # S3 раньше S1, S2 — прыжок
    reset = at_d - timedelta(minutes=1)
    assert reset > at_a + timedelta(minutes=7)
    gone = live.car_view(LDAY, now, f, plan, live.TruckSpec(), DEPOT, live.Rules(), live.Road(), False,
                         sent_at=reset - ac.RESYNC)
    assert 'sequence' in gone['alerts']['active']


def test_build_does_not_rebuild_when_real_deadline_unreachable(monkeypatch):
    ctx, stops, _, _ = _setup()
    calls = []
    real = dp.build

    def counted(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(dp, 'build', counted)
    soft = _with_deadline(ctx, 104, _first_eta(ctx, stops, 104) - 5)          # и к настоящему сроку не успеть
    draft = dp.build(soft, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    assert 104 in draft.no_window and len(calls) == 1
    calls.clear()
    reach = _with_deadline(ctx, 104, _first_eta(ctx, stops, 104) + 8)
    dp.build(reach, stops, None, [HOWO.car_code, FORD.car_code], 'now')
    assert len(calls) == 2                                                      # пересборка с настоящим сроком


def test_reorder_of_other_car_stop_is_foreign_driver(term_until, client, st):
    s, _, cid = term_until
    _, _, h2 = make_terminal(st, car='OTHER', pin='5678', name='Բաբկեն')
    s2 = login(client, h2, pin='5678')
    e = _reorder([cid[900001], cid[900002]])
    assert post(client, s2, e)['accepted'] == [e['id']]
    row = next(x for x in st.store.events_for_day(DEMO, 'reorder') if x['id'] == e['id'])
    assert row['car_code'] == 'OTHER' and row['flags'] == ['foreign', 'no_deadline']
    assert (row['payload']['reason'], row['payload']['reason_sent']) == ('driver', 'until')


def test_no_deadline_is_info_not_flagged_problem(term_until, client, st):
    s, _, cid = term_until
    e = _reorder([cid[900002], cid[900003]])                                    # у 900002 срока нет
    assert post(client, s, e)['accepted'] == [e['id']]
    today = client.get(f'/api/courier/admin/today?date={DEMO}').get_json()
    assert all(x['id'] != e['id'] for x in today['flagged'])
    assert next(c for c in today['cars'] if c['car_code'] == 'TEST')['flagged'] == 0
    js = (ROOT / 'static' / 'js' / 'courier.js').read_text(encoding='utf-8')
    assert "reorder: 'Հերթի փոփոխություն" in js and 'no_deadline:' in js
    assert "courier.js') }}?v=21" in (ROOT / 'templates' / 'courier.html').read_text(encoding='utf-8')


def test_plan_lines_done_by_gps_and_latest_move(tmp_path, monkeypatch):
    """Плановая линия: S1 посещена по GPS (не отмечена) до смены — впереди, как на карточке; stale_since — от последней
    смены машины."""
    from flask import Flask
    import route_optimizer
    from route_optimizer import views

    class FakeDb:
        connection_string = 'DRIVER={none};'
    monkeypatch.setenv('ROUTES_OSM_PATH', str(tmp_path / 'no-map.osm.pbf'))
    flask_app = Flask(__name__)
    flask_app.secret_key = 'test'
    route_optimizer.init_app(flask_app, FakeDb(), db_path=str(tmp_path / 'routes.db'))
    state = flask_app.extensions['route_optimizer']
    bundle = replace(state.store.load(), depot=DEPOT)
    draft = dp.Draft(trucks=['CAR1'], trips=[dp.DraftTrip(1, 'CAR1', [10, 11, 12, 13])])
    tr = Track().park(DEPOT, 5).drive(A).park(A, 6).drive(B).park(B, 6)
    moved = tr.t + timedelta(minutes=1)
    stops = _seq_stops(['full', 'pending', 'pending', 'pending'])
    stops[0]['delivered_at'] = T(10).isoformat()
    moves = [{'at': moved.isoformat(), 'trip': 1, 'order': ['S3', 'S1', 'S2'], 'moved': 'S3', 'reason': 'until'},
             {'at': (moved + timedelta(minutes=5)).isoformat(), 'trip': 1, 'order': ['S2', 'S3'], 'moved': 'S2',
              'reason': 'driver'}]
    fleet = {'CAR1': {'stops': stops, 'reorders': moves[:1], 'track': tr.pts}}
    route = views._live_plan_routes(state, bundle, None, LDAY, draft, fleet)['CAR1']
    assert route.trip_stops == ((10, 11, 13, 12),)                            # S1 (GPS) впереди, затем S3
    _, at = views._reordered_lines(draft, {'CAR1': {**fleet['CAR1'], 'reorders': moves}}, DEPOT)
    assert at == {'CAR1': moved + timedelta(minutes=5)}
    # визиты по GPS для линии — пересчёт только при новом треке (не на каждом опросе карты)
    calls = []
    real = views.ac.reconstruct
    monkeypatch.setattr(views.ac, 'reconstruct', lambda *x, **k: calls.append(1) or real(*x, **k))
    views._GPS_VISITED.clear()
    for _ in range(3):
        views._reordered_lines(draft, fleet, DEPOT)
    assert len(calls) == 1
    views._reordered_lines(draft, {'CAR1': {**fleet['CAR1'], 'track': tr.pts + [tr.pts[-1]]}}, DEPOT)
    assert len(calls) == 2


def test_resend_gap_driver_following_phone_not_penalized():
    """Проба ревью: «until»-смена S3 в 09:15 на прежней версии, логист отправил рейс в 09:40, телефон ещё не обновил /day —
    водитель едет по своему порядку S3, S2, S1 (09:45, 09:50): штрафа нет; отправка в 10:00 — тем более."""
    move = replace(_r(15, ['S3', 'S2', 'S1'], 'until'), plan_version='stale0000000')
    served = [('S0', 10), ('S3', 20), ('S2', 45), ('S1', 50)]
    for sent in (40, 60):
        assert ac.reordered_changes(_actual(served), PSTOPS, PLAN, ac.history_reorders([move], PLAN, T(sent))) == (0, 4)

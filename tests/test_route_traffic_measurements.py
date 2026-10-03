from dataclasses import replace
from datetime import date, datetime, timedelta
import math
import sqlite3
import pytest

from route_optimizer import fleet as fl, store as st, measurements as ms
from route_optimizer.evaluate import ActualVisit, Norms, calibrate
from route_optimizer.geo import Fix
from route_optimizer.traffic_validation import TrafficProfile, learn
from test_route_optimizer import client, _dispatch_setup, _dorder, DP_DAY

DEPOT = (40.1792, 44.4991)
POINT = (40.18, 44.51)
NORMS = Norms(540, 1.3, 30, 45, DEPOT, 12, 0, 0)


def test_fifo_across_hour_and_midnight():
    profile = TrafficProfile({(True, 0, 9): .25, (True, 0, 10): 1.5, (True, 1, 0): .4})
    arrivals = [t + profile.travel(10, 30, True, 4, t) for t in range(590, 610)]
    assert arrivals == sorted(arrivals)
    assert profile.travel(5, 30, True, 4, 1435) > 10
    assert profile.travel(0, 30, True, 0, 540) == 0


def test_traffic_changes_eta_and_assignment():
    norms = replace(NORMS, traffic=TrafficProfile({(True, 0, 9): .25}), traffic_start_min=540)
    tn = fl.TruckNorms(12, 0, 0)
    plain = fl.route_day([POINT], [100], [1000], DEPOT, [fl.FleetTruck('T', None, 1000, 10)], NORMS, tn, overflow=False)
    slow = fl.route_day([POINT], [100], [1000], DEPOT, [fl.FleetTruck('T', None, 1000, 10)], norms, tn, overflow=False)
    assert plain and not slow


def test_loading_every_trip_and_heavy_share():
    tn = fl.TruckNorms(540, 0, 0, warehouse_load_fixed_min=10, warehouse_load_min_per_tonne=4)
    rows = fl.route_day([POINT], [2500], [1000], DEPOT, [fl.FleetTruck('T', None, 1000, 10)], NORMS, tn, overflow=False)
    assert len(rows) == 3
    drive = fl.route_trip([POINT], [2500/3], DEPOT, NORMS, fl.TruckNorms(540, 0, 0))[2]
    assert sum(r.minutes for r in rows) == pytest.approx(3*drive + 30 + 10)


def test_loading_precedes_first_customer_and_shift_check():
    tn = fl.TruckNorms(18, 0, 0, warehouse_load_fixed_min=15)
    dep, arrival, minutes = fl.trip_schedule([POINT], [100], DEPOT, NORMS, tn)
    assert dep == 0 and arrival[0] > 15 and minutes > 18
    rows = fl.route_day([POINT], [100], [1000], DEPOT, [fl.FleetTruck('T', None, 1000, 10)], NORMS, tn,
                        windows=[(0, 10)], overflow=False, solver=True)
    assert not rows


def gps_history():
    today = date(2026, 10, 2)
    visits, fixes = [], []
    for n in range(31):
        day = today-timedelta(days=n+1)
        for h in (9, 18):
            start = datetime.combine(day, datetime.min.time())+timedelta(hours=h)
            visits.append(ActualVisit(1, 1, day, start, start+timedelta(minutes=10), *DEPOT, 5, '01'))
        for hour, step in ((10, .006), (17, .002)):
            for minute in range(31):
                at = datetime.combine(day, datetime.min.time())+timedelta(hours=hour, minutes=minute)
                fixes.append(Fix(at, 40.18, 44.55 + (minute % 2)*step, 5))
    return today, visits, {1: fixes}


def test_hourly_model_is_checked_on_separate_week():
    today, visits, fixes = gps_history()
    model = learn(visits, fixes, DEPOT, 12, today)
    assert model.factors and model.report['validation'][0]['accepted']
    assert model.factor(True, 0, 600) > 1 and model.factor(True, 0, 1020) < 1
    assert model.report['train_end'] == '2026-09-25'
    assert model.report['live'] is False
    # Iterable чек-инов не должен потеряться после основной калибровки.
    result = calibrate(iter(visits), fixes, today-timedelta(days=42), today, DEPOT, 12)
    assert result.traffic.factors == model.factors


def test_gps_without_visits_and_single_date_not_learned():
    today, visits, fixes = gps_history()
    assert not learn([], fixes, DEPOT, 12, today).factors
    assert not learn(visits[:2], fixes, DEPOT, 12, today).factors


def test_delayed_loading_keeps_first_window_without_occupying_morning():
    tn = fl.TruckNorms(540, 0, 0, warehouse_load_fixed_min=20)
    dep, arrival, minutes = fl.trip_schedule([POINT], [100], DEPOT, NORMS, tn, windows=[(180,240)])
    assert dep > 150
    assert arrival[0] == pytest.approx(180, abs=1e-6)
    assert minutes < 30


@pytest.mark.parametrize('key,value', [('liters', -1), ('km', math.inf), ('minutes', True), ('trips', 1.5), ('average_load_pct', 101)])
def test_measurements_reject_invalid_input(key, value):
    _, errors = ms.validate({'day':'2026-10-01','car_code':'T',key:value}, {'T'}, date(2026,10,2))
    assert key in errors


def test_no_manufactured_fuel_calibration():
    assert not ms.summary([])['cars']
    rows = [dict(day=(date(2026,9,1)+timedelta(days=n)).isoformat(),car_code='T',km=100,liters=15,
                 average_load_pct=40) for n in range(16)]
    assert ms.summary(rows)['cars'][0]['fuel'] is None


def test_fuel_calibration_validates_last_four_dates():
    rows = [dict(day=(date(2026,9,1)+timedelta(days=n)).isoformat(),car_code='T',km=100,
                 liters=10+10*(n%4)/4, average_load_pct=100*(n%4)/4) for n in range(16)]
    fit = ms.summary(rows)['cars'][0]['fuel']
    assert fit['base'] == 10 and fit['slope'] == 10 and fit['test_days'] == 4
    for row in rows[-4:]: row['liters'] *= 2
    assert ms.summary(rows)['cars'][0]['fuel'] is None


def test_loading_fit_not_derived_from_unload_time():
    rows = [dict(day=(date(2026,9,1)+timedelta(days=n)).isoformat(),car_code='T',trips=2,
                 kg=(1+n%4)*2000, loading_minutes=2*(10+3*(1+n%4))) for n in range(16)]
    fit = ms.summary(rows)['cars'][0]['loading']
    assert fit['base'] == 10 and fit['slope'] == 3


def test_own_journal_and_prediction_retained(client):
    _dispatch_setup(client, [_dorder(1, 101, 400)])
    body = {'day':DP_DAY.isoformat(),'car_code':'CAR1','km':123,'liters':20}
    r = client.post('/api/routes/measurements', json=body)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['records'] == 1
    body['liters'] = 21
    r = client.post('/api/routes/measurements', json=body)
    assert r.status_code == 200 and r.get_json()['records'] == 1
    assert r.get_json()['validation'][0]['metrics']['liters']['actual'] == 21
    assert r.get_json()['validation'][0]['metrics']['liters']['error_pct'] is None


def test_prospective_prediction_is_saved_on_build(client, monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, '_clock', lambda: datetime.combine(DP_DAY-timedelta(days=1), datetime.min.time())+timedelta(hours=17))
    _dispatch_setup(client, [_dorder(1, 101, 400)])
    r = client.post('/api/routes/dispatch/build', json={'date':DP_DAY.isoformat(),'trucks':['CAR1']})
    assert r.status_code == 200, r.get_json()
    state = client.application.extensions['route_optimizer']
    raw, _ = state.store.load_dispatch(DP_DAY.isoformat())
    assert raw['prediction']['trucks']['CAR1']['liters'] > 0
    assert raw['prediction']['prospective'] is True


def test_new_settings_validation(client):
    r = client.post('/api/routes/settings', json={'settings': {'warehouse_load_fixed_min':20, 'warehouse_load_min_per_tonne':5, 'traffic_mode':'static'}})
    assert r.status_code == 200
    r = client.post('/api/routes/settings', json={'settings': {'warehouse_load_fixed_min': -1}})
    assert r.status_code == 400


def test_provider_validates_every_directed_pair_and_batches():
    from route_optimizer.traffic_provider import fetch
    from zoneinfo import ZoneInfo
    calls = []
    def get(url, params, **kwargs):
        origins = [tuple(map(float,p.split(','))) for p in params['origins'].split('|')]
        dest = [tuple(map(float,p.split(','))) for p in params['destinations'].split('|')]
        assert len(origins)*len(dest) <= 100
        assert params['mode'] == 'driving' and 'traffic' not in params
        assert kwargs['allow_redirects'] is False
        calls.append(params['departure_time'])
        class Reply:
            status_code = 200
            def json(self):
                return {'rows':[{'elements':[{'status':'OK','distance':{'value':1000+int(a[1]*100)},
                    'duration':{'value':300+int(b[1]*10)}} for b in dest]} for a in origins]}
        return Reply()
    pts = [(40.18,44.5+i*.001) for i in range(11)]
    matrix = fetch(pts, datetime(2026,10,3,9,tzinfo=ZoneInfo('Asia/Yerevan')), 'private-test-key', get=get)
    assert len(calls) == 4 and len(matrix.durations) == 121
    assert matrix.minutes(pts[0],pts[0]) == 0
    norms = replace(NORMS, provider=matrix)
    _, m = fl._matrices(pts[1:3], pts[0], norms, fl.TruckNorms(540,0,0))
    assert m.travel(0,1,0) == matrix.minutes(pts[0],pts[1])
    assert 'private-test-key' not in repr(matrix)


def test_provider_failures_cannot_claim_live_data(monkeypatch):
    from route_optimizer.traffic_provider import load
    monkeypatch.delenv('ROUTES_YANDEX_API_KEY', raising=False)
    value, status = load([POINT, DEPOT], date(2026,10,3), '09:00')
    assert value is None and status['reason'] == 'missing_api_key' and not status['live']
    monkeypatch.setenv('ROUTES_YANDEX_API_KEY', 'private-test-key')
    value, status = load([POINT, DEPOT], date(2020,1,1), '09:00')
    assert value is None and status['reason'] == 'departure_in_past'


def test_csv_includes_missing_clients_without_erp_writes(client):
    _dispatch_setup(client, [_dorder(1,999,400, day=DP_DAY-timedelta(days=1))])
    r = client.get('/api/routes/coordinates/missing.csv')
    assert r.status_code == 200
    assert r.data.startswith(b'\xef\xbb\xbf')
    assert b'999' in r.data

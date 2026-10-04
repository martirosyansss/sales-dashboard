# -*- coding: utf-8 -*-
"""«Ավտոտնակ» → вкладка «Նորմա և փաստ» (просьба владельца 04.10): расход по заправкам против нормы «Развоза», км по GPS
против плана, стоянки вне плана и точки не по порядку — за месяц; карта дня. Доступ — роль «Гараж» (и снаружи),
администратор; остальным — нет.

Правило месяца для расхода: интервал «полный бак → полный бак» идёт целиком в месяц закрывающей заправки (день по
Еревану), как learning.fuel_obs — литры и км через границу месяца не делятся. Флаг — факт выше нормы (плана) больше чем
на garage.ALERT_PCT = 10% по Δ% до 0,1 (ровно +10,0% — без флага).

Факт — синтетический (FakeFacts из test_learning_loop); базы — временные; ERP не читается.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import garage  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_garage_role import _login_as, dashboard  # noqa: E402,F401
from test_learning_loop import TODAY, TZ, FakeFacts  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

D = date


# ============================== чистые правила ==============================

def test_delta_and_flag_boundary():
    assert garage.ALERT_PCT == 10
    assert garage.delta_pct(33.0, 30.0) == 10.0 and not garage.over(33.0, 30.0)      # ровно +10% — не флаг
    assert garage.delta_pct(30.8, 28.0) == 10.0 and not garage.over(30.8, 28.0)      # 10,000…02% в float — тоже нет
    assert garage.delta_pct(33.04, 30.0) == 10.1 and garage.over(33.04, 30.0)
    assert garage.delta_pct(25.0, 30.0) == -16.7 and not garage.over(25.0, 30.0)     # ниже нормы — не флаг
    for fact, norm in ((None, 30.0), (30.0, None), (30.0, 0.0), (30.0, -1.0)):
        assert garage.delta_pct(fact, norm) is None and not garage.over(fact, norm)


def test_fuel_month_attribution_and_weighting():
    ivs = [(D(2026, 9, 20), 150.0, 500.0), (D(2026, 9, 28), 99.0, 300.0), (D(2026, 10, 2), 105.0, 300.0)]
    days = [D(2026, 9, 10), D(2026, 9, 20), D(2026, 9, 28), D(2026, 10, 2)]
    sep = garage.fuel_month(ivs, days, D(2026, 9, 1), D(2026, 9, 30))
    # Σ литров / Σ км (249 / 800), а не среднее расходов интервалов (31,5)
    assert (sep.l100, sep.liters, sep.km, sep.intervals, sep.refuels, sep.reason) == (31.1, 249.0, 800.0, 2, 3, None)
    # интервал 28.09 → 02.10 — целиком в октябре (месяц закрывающей заправки); в октябре одна заправка, но расход есть
    oct_ = garage.fuel_month(ivs, days, D(2026, 10, 1), D(2026, 10, 31))
    assert (oct_.l100, oct_.liters, oct_.km, oct_.intervals, oct_.refuels, oct_.reason) == (35.0, 105.0, 300.0, 1, 1, None)
    # границы месяца включительно
    assert garage.fuel_month([(D(2026, 9, 30), 30.0, 100.0)], [], D(2026, 9, 1), D(2026, 9, 30)).l100 == 30.0
    assert garage.fuel_month([(D(2026, 9, 1), 30.0, 100.0)], [], D(2026, 9, 1), D(2026, 9, 30)).l100 == 30.0
    assert garage.fuel_month([(D(2026, 10, 1), 30.0, 100.0)], [], D(2026, 9, 1), D(2026, 9, 30)).l100 is None


@pytest.mark.parametrize('days,reason', [([], 'no_refuels'), ([D(2026, 8, 31), D(2026, 10, 1)], 'no_refuels'),
                                         ([D(2026, 9, 5)], 'one_refuel'),
                                         ([D(2026, 9, 5), D(2026, 9, 9)], 'no_interval')])
def test_fuel_month_no_data_reasons(days, reason):
    got = garage.fuel_month([], days, D(2026, 9, 1), D(2026, 9, 30))
    assert got.l100 is None and got.reason == reason and got.intervals == 0


def _report(km_plan, km_fact, unplanned=0, order=0):
    return {'plan': {'km': km_plan}, 'fact': {'km': km_fact, 'unplanned_stays': unplanned},
            'kpi': {'order_changes': order}}


def test_km_month_only_days_with_plan_and_gps():
    got = garage.km_month([_report(20.0, 25.0, 1, 2), _report(None, 40.0, 3, 0), _report(10.0, 11.0, 0, 1)])
    # км сравниваются только в дни с планом; стоянки и порядок — за все дни с треком
    assert got == garage.KmMonth(3, 2, 30.0, 36.0, 4, 3)
    assert garage.km_month([]) == garage.KmMonth(0, 0, 0.0, 0.0, 0, 0)


# ============================== API: расчёт на синтетическом факте ==============================

def _rf(i, day, odo, liters, full=True, car='CAR1', superseded=False):
    return {'id': f'r{i:03d}', 'car_code': car, 'date': day, 'at_utc': f'{day}T06:00:00.000000+00:00',
            'payload': {'odometer_km': odo, 'liters': liters, 'full_tank': full}, 'flags': [],
            'superseded': superseded}


REFUELS = [_rf(1, '2026-09-10', 10000, 50), _rf(2, '2026-09-20', 10500, 150), _rf(3, '2026-09-28', 10800, 99),
           _rf(4, '2026-10-02', 11100, 105),
           _rf(5, '2026-09-25', 7000, 40, car='CAR2'),                                  # одна заправка в сентябре
           _rf(6, '2026-10-01', 7100, 999, car='CAR2', superseded=True)]                # исправлена — не в счёт


class Facts(FakeFacts):
    def __init__(self, days, refuels=()):
        super().__init__(days)
        self._refuels = list(refuels)

    def refuels(self):
        return list(self._refuels)


def _norm_client(client, monkeypatch, refuels=REFUELS):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    # трек CAR1 каждый день 20.09 … 03.10 (сегодня — тоже: в отчёт не идёт, день не кончился)
    state.fleet_facts = Facts([TODAY - timedelta(days=i) for i in range(13, -1, -1)], refuels)
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 10, 0, tzinfo=TZ))
    return state


def _truck(body, code):
    return next(t for t in body['trucks'] if t['car_code'] == code)


def test_norm_month_fuel_km_and_boundaries(client, monkeypatch):
    state = _norm_client(client, monkeypatch)
    sep = client.get('/api/routes/garage/norm?month=2026-09').get_json()
    assert sep['success'] and (sep['month'], sep['from'], sep['to'], sep['gps_to']) == \
        ('2026-09', '2026-09-01', '2026-09-30', '2026-09-30')
    assert sep['rules'] == {'alert_pct': 10, 'fuel_min_km': lr.FUEL_MIN_KM} and sep['has_data'] and sep['connected']
    car1 = _truck(sep, 'CAR1')
    assert car1['norm'] == {'l100': 30.0, 'source': 'manual'}
    assert car1['fuel'] == {'l100': 31.1, 'liters': 249.0, 'km': 800.0, 'intervals': 2, 'refuels': 3, 'reason': None,
                            'delta_pct': 3.7, 'over': False}
    # трек 20…30.09 — 11 дней; плана «Развоза» нет — км не сравниваются
    assert car1['km']['days'] == 11 and car1['km']['plan_days'] == 0 and car1['km']['delta_pct'] is None
    assert not car1['km']['over'] and len(car1['days']) == 11
    assert [d['day'] for d in car1['days']][:2] == ['2026-09-30', '2026-09-29']        # новые первыми
    fact_km = car1['days'][0]['fact_km']
    assert fact_km > 0 and car1['days'][0]['plan_km'] is None and car1['days'][0]['liters'] is not None
    car2 = _truck(sep, 'CAR2')
    assert car2['fuel']['l100'] is None and car2['fuel']['reason'] == 'one_refuel' and car2['km']['days'] == 0
    # октябрь (текущий, по умолчанию): интервал 28.09 → 02.10 — здесь; трек — 1 и 2.10, сегодня не входит
    oct_ = client.get('/api/routes/garage/norm').get_json()
    assert (oct_['month'], oct_['gps_to'], oct_['current_month']) == ('2026-10', '2026-10-02', '2026-10')
    car1 = _truck(oct_, 'CAR1')
    assert car1['fuel']['l100'] == 35.0 and car1['fuel']['refuels'] == 1 and car1['fuel']['delta_pct'] == 16.7
    assert car1['fuel']['over'] and [d['day'] for d in car1['days']] == ['2026-10-02', '2026-10-01']
    assert _truck(oct_, 'CAR2')['fuel']['reason'] == 'no_refuels'      # исправленная заправка не считается
    assert oct_['trucks'][0]['car_code'] == 'CAR1'                    # с флагом — первой

    # план «Развоза» на 29.09: км плана меньше факта на 25% — флаг; 30.09: план = факт — без флага
    state.store.save_dispatch('2026-09-29', {'trips': [{'truck': 'CAR1', 'stops': [101, 102]},
                                                       {'truck': 'CAR1', 'stops': [104]}],
                                             'prediction': {'trucks': {'CAR1': {'km': round(fact_km / 1.25, 1)}}}}, 'qa')
    state.store.save_dispatch('2026-09-30', {'trips': [{'truck': 'CAR1', 'stops': [101, 102, 104]}],
                                             'prediction': {'trucks': {'CAR1': {'km': fact_km}}}}, 'qa')
    sep = client.get('/api/routes/garage/norm?month=2026-09').get_json()
    km = _truck(sep, 'CAR1')['km']
    plan = round(fact_km / 1.25, 1) + fact_km
    assert km['days'] == 11 and km['plan_days'] == 2
    assert km['plan'] == pytest.approx(plan) and km['fact'] == pytest.approx(2 * fact_km)
    assert km['delta'] == pytest.approx(round(2 * fact_km - plan, 1))
    assert km['delta_pct'] == garage.delta_pct(km['fact'], km['plan']) and km['delta_pct'] > 10 and km['over']
    by_day = {d['day']: d for d in _truck(sep, 'CAR1')['days']}
    assert by_day['2026-09-29']['over'] and not by_day['2026-09-30']['over'] and not by_day['2026-09-28']['over']
    # 30.09: план — один рейс 101 → 102 → 104, факт — два (склад между 102 и 104): порядок тот же
    assert by_day['2026-09-30']['order_changes'] == 0


def test_norm_learned_fuel_is_the_norm(client, monkeypatch):
    state = _norm_client(client, monkeypatch)
    state.store.save_learned('2026-10-01', [lr.Outcome('fuel', 'CAR1', True, 'ok', {'empty_l100': 24.0,
                                                                                     'full_l100': 32.0})])
    car1 = _truck(client.get('/api/routes/garage/norm').get_json(), 'CAR1')
    # «Развоз» считает CAR1 по выученному: середина «пустой — полный» (learning.apply_learned)
    assert car1['norm'] == {'l100': 28.0, 'source': 'learned'} and car1['fuel']['delta_pct'] == 25.0
    # автообучение расхода выключено — снова ручная норма из настроек
    state.store.save_learning_auto('fuel', False, 'qa')
    assert _truck(client.get('/api/routes/garage/norm').get_json(), 'CAR1')['norm'] == {'l100': 30.0,
                                                                                         'source': 'manual'}


def test_norm_without_courier_data_is_empty_state(client, monkeypatch):
    state = _norm_client(client, monkeypatch, refuels=())
    state.fleet_facts = None
    body = client.get('/api/routes/garage/norm').get_json()
    assert body['success'] and not body['connected'] and not body['has_data']
    car1 = _truck(body, 'CAR1')
    assert car1['norm']['l100'] == 30.0 and car1['fuel']['reason'] == 'no_refuels' and car1['km']['days'] == 0
    assert car1['days'] == []
    state.fleet_facts = Facts([])                                       # «Առաքիչ» есть, данных нет
    body = client.get('/api/routes/garage/norm').get_json()
    assert body['connected'] and not body['has_data']


def test_norm_cold_month_is_counted_in_background(client, monkeypatch):
    """Факт месяца не успел посчитаться за GARAGE_NORM_WAIT_S — pending, досчитывается одним потоком в фоне (в кэш
    _learning_days); следующий запрос — готовый ответ без пересчёта."""
    import threading
    state = _norm_client(client, monkeypatch)
    gate, calls = threading.Event(), []
    day = state.fleet_facts.day

    def slow(car, ds):
        calls.append(ds)
        gate.wait(10)
        return day(car, ds)
    monkeypatch.setattr(state.fleet_facts, 'day', slow)
    monkeypatch.setattr(views, 'GARAGE_NORM_WAIT_S', 0.05)
    for _ in range(2):                                                  # повтор не запускает второй поток
        assert client.get('/api/routes/garage/norm?month=2026-09').get_json() == {'success': True, 'pending': True,
                                                                                 'month': '2026-09'}
    assert len(state.garage_warm) == 1
    gate.set()
    monkeypatch.setattr(views, 'GARAGE_NORM_WAIT_S', 10)
    body = client.get('/api/routes/garage/norm?month=2026-09').get_json()
    assert body['pending'] is False and _truck(body, 'CAR1')['km']['days'] == 11
    assert len(calls) == 11 and not state.garage_warm                   # каждый день посчитан один раз


@pytest.mark.parametrize('month', ['2026-11', '2027-01', '2026-13', '2026-00', '1999-12', '2026-9', 'x', '2026-10-01'])
def test_norm_month_validation(client, monkeypatch, month):
    _norm_client(client, monkeypatch)
    r = client.get('/api/routes/garage/norm?month=' + month)
    assert r.status_code == 400 and 'month' in r.get_json()['errors']


def test_garage_day_map_is_learning_day(client, monkeypatch):
    _norm_client(client, monkeypatch)
    got = client.get('/api/routes/garage/day?date=2026-09-30&car=CAR1').get_json()
    assert got == client.get('/api/routes/learning/day?date=2026-09-30&car=CAR1').get_json()
    assert got['success'] and len(got['track']) > 1 and got['km_gps'] > 0 and len(got['stops']) == 3
    assert client.get('/api/routes/garage/day?date=x&car=CAR1').status_code == 400


# ============================== доступ ==============================

def test_norm_access_by_role(dashboard, monkeypatch):
    c = dashboard
    state = c.application.extensions['route_optimizer']
    monkeypatch.setattr(state, 'fleet_facts', None)
    for path in ('/api/routes/garage/norm', '/api/routes/garage/norm?month=2026-09',
                 '/api/routes/garage/day?date=2026-10-01&car=CAR1'):
        r = c.get(path)
        assert r.status_code == 401 and r.get_json()['success'] is False, path       # без входа
    h = _login_as(c, 'garage1')
    assert c.get('/api/routes/garage/norm').status_code == 200
    r = c.get('/api/routes/garage/day?date=2026-10-01&car=CAR1')
    assert r.status_code == 400 and 'Առաքիչ' in r.get_json()['errors']['_']          # проверка дошла до раздела
    # гаражу — только своя карта: API «Обучения» и линии по дорогам закрыты
    assert c.get('/api/routes/learning/day?date=2026-10-01&car=CAR1').status_code == 403
    assert c.get('/api/routes/learning').status_code == 403
    assert c.post('/api/routes/road-lines', json={'lines': []}, headers=h).status_code == 403
    _login_as(c, 'boss')
    assert c.get('/api/routes/garage/norm').status_code == 200
    _login_as(c, 'u')
    assert c.get('/api/routes/garage/norm').status_code == 403
    assert c.get('/api/routes/garage/day?date=2026-10-01&car=CAR1').status_code == 403


def test_public_allowlist_covers_norm_and_basemap_only():
    import app_v2
    for path in ('/api/routes/garage/norm', '/api/routes/garage/day', '/static/js/routes_basemap.js'):
        assert app_v2._public_path_allowed(path, 'GET'), path
    assert not app_v2._public_path_allowed('/static/js/routes_basemap.js', 'POST')
    for path in ('/api/routes/learning/day', '/api/routes/learning', '/api/routes/road-lines',
                 '/static/js/routes_learning.js', '/static/js/routes_dispatch.js', '/static/img/yandex_maps_logo_ru.svg',
                 '/static/js/routes_basemap.js/', '/static/js/Routes_basemap.js', '/api/routes/garage/norm/',
                 '/api/routes/garage/Norm'):
        assert not app_v2._public_path_allowed(path, 'GET'), path
    assert app_v2._PUBLIC_STATIC == frozenset((
        '/favicon.ico', '/static/css/tokens.css', '/static/css/base.css', '/static/js/base.js', '/static/css/routes.css',
        '/static/css/routes_garage.css', '/static/js/routes_garage.js', '/static/js/routes_basemap.js'))


def test_garage_page_tab_and_basemap_without_yandex_key(dashboard, monkeypatch):
    monkeypatch.setenv('ROUTES_YANDEX_TILES_KEY', '0b6a3f1c-5d2e-4f7a-9c8b-1e2d3c4b5a69')
    _login_as(dashboard, 'garage1')
    html = dashboard.get('/routes/garage').get_data(as_text=True)
    assert '0b6a3f1c' not in html                                       # ключ Яндекса странице гаража не выдаётся
    assert 'role="tab" id="gjTabNorm" aria-controls="gjNorm" aria-selected="false" tabindex="-1"' in html
    assert 'id="gjNorm" role="tabpanel" aria-labelledby="gjTabNorm" hidden' in html and 'Նորմա և փաստ' in html
    leaflet = html.index('leaflet@1.9.4/dist/leaflet.js')
    basemap = html.index('js/routes_basemap.js?v=1" data-yandex-key=""')
    assert leaflet < basemap < html.index('js/routes_garage.js?v=7')
    js = (ROOT / 'static' / 'js' / 'routes_garage.js').read_text(encoding='utf-8')
    assert "'/api/routes/garage/norm'" in js and "'/api/routes/garage/day?date='" in js
    assert '/api/routes/learning' not in js and 'road-lines' not in js
    assert "['gjTabNorm', 'gjNorm']" in js and 'garage.ALERT_PCT' not in js and 'd.rules.alert_pct' in js

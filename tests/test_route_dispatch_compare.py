# -*- coding: utf-8 -*-
"""«Развоз»: сравнение вариантов плана (ответ владельца №83, как what-if у MaxOptra / WorkWave). POST
/api/routes/dispatch/compare — рейсы дня другим набором машин только в памяти (короткий решатель), итог для сравнения;
ничего не сохраняет; утверждённый план тоже сравнивается («если пересобрать»). Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_compare.py -q
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_dispatch_same_day import DAY, _build, _page_setup, client  # noqa: E402,F401

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
KEYS = {'trucks', 'trips', 'stops', 'unassigned', 'unassigned_kg', 'kg', 'km', 'liters', 'cost_amd', 'last_return',
        'over_time', 'window_miss'}


def _compare(client, trucks, day=DAY):
    return client.post('/api/routes/dispatch/compare', json={'date': day, 'trucks': trucks})


def test_compare_variants_without_saving(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    before = state.store.load_dispatch(DAY)
    one = _compare(client, ['CAR2'])
    assert one.status_code == 200, one.get_json()
    s1 = one.get_json()['summary']
    assert set(s1) == KEYS and set(s1['trucks']) <= {'CAR2'} and s1['stops'] + s1['unassigned'] >= 1
    assert s1['last_return'] is None or views._return_min(s1['last_return']) is not None   # формат возвращения рейса
    two = _compare(client, ['CAR2', 'CAR1', 'CAR1']).get_json()
    assert two['trucks'] == ['CAR1', 'CAR2']                       # набор без повторов, по коду
    assert two['summary']['stops'] >= s1['stops']                  # две машины развезут не меньше одной
    assert state.store.load_dispatch(DAY) == before               # ничего не сохранено (тот же черновик и rev)
    assert d['rev'] == before[1]


def test_compare_before_first_build_saves_nothing(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    assert state.store.load_dispatch(DAY) is None
    r = _compare(client, ['CAR1'])
    assert r.status_code == 200, r.get_json()
    assert set(r.get_json()['summary']) == KEYS
    assert state.store.load_dispatch(DAY) is None


def test_compare_validation(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1', 'CAR2'))
    for bad in ([], 'CAR1', None, [1], ['CAR1', None], ['CAR1'] * (views._MAX_TRUCKS + 1)):
        r = _compare(client, bad)
        assert r.status_code == 400 and 'trucks' in r.get_json()['errors'], bad
    assert _compare(client, []).get_json()['errors']['trucks'] == 'отметьте хотя бы одну машину'
    r = _compare(client, ['NOPE', 'CAR1'])
    assert r.status_code == 400 and r.get_json()['errors']['trucks'] == 'машина не готова к расчёту: NOPE'
    assert _compare(client, ['CAR1'], day='01.10.2026').status_code == 400
    assert client.post('/api/routes/dispatch/compare', json=['CAR1']).status_code == 400


def test_compare_on_approved_plan_rebuilds_freely(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    before = state.store.load_dispatch(DAY)
    r = _compare(client, ['CAR2'])
    assert r.status_code == 200, r.get_json()                     # утверждение не мешает «если пересобрать»
    assert state.store.load_dispatch(DAY) == before and before[0]['approved']   # утверждение в базе — как было


def test_compare_keeps_loaded_trip_truck(client, monkeypatch):
    """№78: загруженный рейс пересборка не трогает — набор без его машины не считается (400, как у сборки)."""
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    truck = next(t for t in d['plan']['trucks'] if t['trips'])
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'loaded',
                                                       'trip': truck['trips'][0]['id']})
    assert r.status_code == 200, r.get_json()
    before = state.store.load_dispatch(DAY)
    other = [c for c in ('CAR1', 'CAR2') if c != truck['car_code']]
    r = _compare(client, other)
    assert r.status_code == 400 and views.LOADED_TRUCK_OFF in r.get_json()['error']
    assert _compare(client, [truck['car_code']]).status_code == 200
    assert state.store.load_dispatch(DAY) == before


def test_compare_uses_short_solver(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1', 'CAR2'))
    seen = []
    real = views.dp.build
    monkeypatch.setattr(views.dp, 'build', lambda *a, **k: seen.append(k.get('iterations')) or real(*a, **k))
    assert _compare(client, ['CAR1']).status_code == 200
    assert seen == [views.COMPARE_ITERATIONS]


def test_compare_summary_last_return_after_midnight():
    trip = {'over_time': False}
    view = {'summary': {'trips': 3, 'stops': 4, 'kg': 900, 'km': 51.5, 'liters': 9.2, 'operating_cost_amd': 12000,
                        'window_miss': 2},
            'trucks': [{'car_code': 'A', 'return': '23:50', 'trips': [trip]},
                       {'car_code': 'B', 'return': '00:40 (+1)', 'trips': [trip, {'over_time': True}]},
                       {'car_code': 'C', 'return': None, 'trips': []}],
            'unassigned': [{'kg': 10.4}, {'kg': None}]}
    s = views._compare_summary(view)
    assert s['trucks'] == ['A', 'B'] and s['last_return'] == '00:40 (+1)'   # после полуночи — позже, чем 23:50
    assert (s['over_time'], s['window_miss'], s['unassigned'], s['unassigned_kg'], s['cost_amd']) == (1, 2, 2, 10, 12000)


def test_page_has_compare_dialog_texts():
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    for marker in ('id="dpCompare"', 'id="dpMenuCompare"', 'id="dpCmpDlg"', 'Համեմատել տարբերակները', 'id="dpCmpTable"',
                   'id="dpCmpTrucks"', 'id="dpCmpRun"', 'id="dpCmpClose"'):
        assert marker in page, marker
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert "'/api/routes/dispatch/compare'" in js and "$('dpCmpDlg').open" in js   # открытый диалог — без автообновления
    for word in ('Ընթացիկ պլանը', 'Վերակազմել նույն մեքենաներով', 'Իմ տարբերակը', 'Կիրառել'):
        assert word in js, word

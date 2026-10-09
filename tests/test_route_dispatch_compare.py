# -*- coding: utf-8 -*-
"""«Развоз»: сравнение вариантов плана (ответ владельца №83, как what-if у MaxOptra / WorkWave). POST
/api/routes/dispatch/compare — рейсы дня другим набором машин, как «Վերակազմել» (водители дня №77, закрепления логиста),
но с коротким решателем и только в памяти; ничего не сохраняет; утверждённый план тоже сравнивается («если пересобрать»);
план изменён в другой вкладке — 409; расчётов сразу не больше двух — 429. Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_compare.py -q
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_dispatch_same_day import DAY, _build, _page_setup, client  # noqa: E402,F401

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=ac.YEREVAN)
KEYS = {'trucks', 'trips', 'stops', 'unassigned', 'km', 'liters', 'cost_amd', 'last_return', 'over_time', 'window_miss',
        'no_driver'}


def _rev(client):
    return client.get(f'/api/routes/dispatch?date={DAY}').get_json()['rev']


def _compare(client, trucks, day=DAY, **extra):
    body = {'date': day, 'trucks': trucks, **extra}
    body.setdefault('rev', _rev(client))
    return client.post('/api/routes/dispatch/compare', json=body)


def test_compare_variants_without_saving(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    before = state.store.load_dispatch(DAY)
    one = _compare(client, ['CAR2'])
    assert one.status_code == 200, one.get_json()
    s1 = one.get_json()['summary']
    assert set(s1) == KEYS and set(s1['trucks']) <= {'CAR2'} and s1['stops'] + s1['unassigned'] >= 1
    assert s1['no_driver'] == [] and (s1['last_return'] is None or views._return_min(s1['last_return']) is not None)
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
    r = _compare(client, ['CAR1'], agents_off='2')
    assert r.status_code == 400 and r.get_json()['errors']['agents_off'] == 'ожидался список менеджеров'


def test_compare_stale_rev_is_409(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    for rev in (d['rev'] - 1, None, str(d['rev'])):
        r = _compare(client, ['CAR1'], rev=rev)
        assert r.status_code == 409 and r.get_json()['conflict'] is True, rev


def test_compare_on_approved_plan_rebuilds_freely(client, monkeypatch):
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    d = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'approve'}).get_json()
    before = state.store.load_dispatch(DAY)
    r = _compare(client, ['CAR2'])
    assert r.status_code == 200, r.get_json()                     # утверждение не мешает «если пересобрать»
    assert state.store.load_dispatch(DAY) == before and before[0]['approved']   # утверждение в базе — как было


def test_compare_keeps_logist_pins(client, monkeypatch):
    """Закреплённый логистом рейс остаётся в варианте как есть — как у «Վերակազմել»."""
    _page_setup(client, monkeypatch, now=NOW)
    d = _build(client, ('CAR1', 'CAR2'))
    truck = next(t for t in d['plan']['trucks'] if t['trips'])
    trip = truck['trips'][0]
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], 'action': 'pin', 'trip': trip['id'],
                                                       'truck': truck['car_code']})
    assert r.status_code == 200, r.get_json()
    seen = []
    real = views.dp.plan_view
    monkeypatch.setattr(views.dp, 'plan_view', lambda *a, **k: seen.append(a[2]) or real(*a, **k))
    assert _compare(client, ['CAR1', 'CAR2']).status_code == 200
    got = next(t for t in seen[-1].trips if t.id == trip['id'])
    assert got.pinned and got.truck == truck['car_code'] and got.stops == [s['customer_id'] for s in trip['stops']]


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


def test_compare_seats_drivers_like_rebuild(client, monkeypatch):
    """№77: водитель CAR2 не вышел, свободных нет — вариант с CAR1 и CAR2 везёт одной CAR1, CAR2 — в no_driver
    (столбец «առանց վարորդի»), как сделает «Վերակազմել»."""
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1',))
    state.store.save_truck_driver('CAR1', DAY, 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', DAY, 'Կարեն', 'qa')
    state.store.save_driver_absence('Կարեն', DAY, None, 'qa')
    s = _compare(client, ['CAR1', 'CAR2']).get_json()['summary']
    assert s['no_driver'] == ['CAR2'] and s['trucks'] == ['CAR1']
    assert _compare(client, ['CAR1']).get_json()['summary']['no_driver'] == []


def test_compare_page_manager_filter(client, monkeypatch):
    """Фильтр «Մենեջերներ», ещё не применённый к плану (agents_off), — точки варианта по нему; черновик не меняется."""
    state, _ = _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1', 'CAR2'))
    before = state.store.load_dispatch(DAY)
    full = _compare(client, ['CAR1', 'CAR2']).get_json()['summary']
    off = _compare(client, ['CAR1', 'CAR2'], agents_off=[2]).get_json()['summary']
    assert full['stops'] + full['unassigned'] == 3 and off['stops'] + off['unassigned'] == 1   # у менеджера 2 — 102, 104
    assert state.store.load_dispatch(DAY) == before


def test_compare_busy_is_429(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1', 'CAR2'))
    taken = 0
    try:
        while views._COMPARE_SLOTS.acquire(blocking=False):
            taken += 1
        r = _compare(client, ['CAR1'])
        assert r.status_code == 429 and r.get_json() == {'success': False, 'error': views.COMPARE_BUSY}
    finally:
        for _ in range(taken):
            views._COMPARE_SLOTS.release()
    assert taken == 2 and _compare(client, ['CAR1']).status_code == 200    # места вернулись — и после ошибки сборки


def test_compare_uses_short_solver(client, monkeypatch):
    _page_setup(client, monkeypatch, now=NOW)
    _build(client, ('CAR1', 'CAR2'))
    seen = []
    real = views.dp.build
    monkeypatch.setattr(views.dp, 'build', lambda *a, **k: seen.append(k.get('iterations', a[5] if len(a) > 5 else None))
                        or real(*a, **k))
    assert _compare(client, ['CAR1']).status_code == 200
    assert seen == [views.COMPARE_ITERATIONS]


def test_compare_summary_last_return_after_midnight():
    trip = {'over_time': False}
    view = {'summary': {'trips': 3, 'stops': 4, 'km': 51.5, 'liters': 9.2, 'operating_cost_amd': 12000, 'window_miss': 2},
            'trucks': [{'car_code': 'A', 'return': '23:50', 'trips': [trip]},
                       {'car_code': 'B', 'return': '00:40 (+1)', 'trips': [trip, {'over_time': True}]},
                       {'car_code': 'C', 'return': None, 'trips': []}],
            'unassigned': [{'kg': 10.4}, {'kg': None}]}
    s = views._compare_summary(view, dp.Draft(unmanned={'D': 'absent'}))
    assert s['trucks'] == ['A', 'B'] and s['last_return'] == '00:40 (+1)'   # после полуночи — позже, чем 23:50
    assert (s['over_time'], s['window_miss'], s['unassigned'], s['cost_amd'], s['no_driver']) == (1, 2, 2, 12000, ['D'])


def test_page_has_compare_dialog_texts():
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    for marker in ('id="dpCompare"', 'id="dpMenuCompare"', 'id="dpCmpDlg"', 'Համեմատել տարբերակները', 'id="dpCmpTable"',
                   'id="dpCmpTrucks"', 'id="dpCmpRun"', 'id="dpCmpClose"'):
        assert marker in page, marker
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert "'/api/routes/dispatch/compare'" in js and "$('dpCmpDlg').open" in js   # открытый диалог — без автообновления
    for word in ('Ընթացիկ պլանը', 'Վերակազմել նույն մեքենաներով', 'Իմ տարբերակը', 'Կիրառել', 'Կրկնել', 'առանց վարորդի'):
        assert word in js, word
    assert f"'{views.COMPARE_BUSY}'" in js                         # 429 — по-армянски (SERVER_HY)

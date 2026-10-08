"""Магазин вне шаблонов менеджеров ERP, но с заказом в «Развозе» (08.10: «ՍԱՍ ս/մ /Բաղրամյան» на 2-м рейсе —
«Մինչև ժամը», «Առաքման պայմաններ» и «Ժամանակ խանութում» показывали «Խանութը չի գտնվել»): его находят и сохраняют
по клиентам заказов «Развоза» в памяти, как магазины снимка."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_route_optimizer import _dispatch_setup, _dorder, client  # noqa: E402,F401

OFF_PLAN = 999      # в заказах дня (_dispatch_setup: «C999», «Новый»), в шаблонах менеджеров — нет
WINDOW = {'kind': 'before', 't1': 600, 't2': None, 'tol': None}


def _row(client, cid):
    r = client.get(f'/api/routes/customer-vehicles?customer_id={cid}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['customers']


def _snapshot_customers(client):
    state = client.application.extensions['route_optimizer']
    return state.snapshots.get(allow_stale=True)[0].customers


def test_off_plan_store_found_and_saved_after_dispatch_loads(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(4, OFF_PLAN, 50.0)])
    assert OFF_PLAN not in _snapshot_customers(client)
    # заказов «Развоза» в памяти ещё нет — магазина не знаем, ничего не пишем
    assert _row(client, OFF_PLAN) == []
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': OFF_PLAN, 'unload_min': 7}).status_code == 400

    assert client.get('/api/routes/dispatch?date=2026-10-01').status_code == 200
    [row] = _row(client, OFF_PLAN)
    assert (row['customer_id'], row['code'], row['name'], row['window'], row['unload_min']) == \
        (OFF_PLAN, 'C999', 'Новый', None, None)

    assert client.post('/api/routes/customer-vehicles', json={'customer_id': OFF_PLAN, 'unload_min': 7}).status_code == 200
    r = client.post('/api/routes/customer-vehicles',
                    json={'customer_id': OFF_PLAN, 'access': None, 'window': WINDOW, 'solo': True, 'center': False})
    assert r.status_code == 200, r.get_json()
    [row] = _row(client, OFF_PLAN)
    assert (row['window'], row['unload_min'], row['solo']) == (WINDOW, 7.0, True)
    # список магазинов с условиями (настройки) и поиск по коду — тоже видят его
    listed = client.get('/api/routes/customer-vehicles').get_json()['customers']
    assert OFF_PLAN in [c['customer_id'] for c in listed]
    assert [c['customer_id'] for c in client.get('/api/routes/customer-vehicles?q=c999').get_json()['customers']] == [OFF_PLAN]


def test_plan_store_unchanged_and_unknown_still_rejected(client):
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    assert client.get('/api/routes/dispatch?date=2026-10-01').status_code == 200
    plan = _snapshot_customers(client)
    cid = next(iter(plan))
    [row] = _row(client, cid)
    assert (row['code'], row['name']) == (plan[cid].code, plan[cid].name)
    # ни в снимке, ни в заказах — по-прежнему «не найден»
    assert _row(client, 123456) == []
    r = client.post('/api/routes/customer-vehicles', json={'customer_id': 123456, 'unload_min': 7})
    assert r.status_code == 400 and 'Խանութը չի գտնվել' in str(r.get_json())


def test_off_plan_store_from_new_orders_of_the_day(client):
    """Магазин только из новых заказов дня (№72, кэш same_day_cache) — тоже находится."""
    import time
    from datetime import date, datetime
    from route_optimizer import dispatch as dp
    _dispatch_setup(client, [])
    state = client.application.extensions['route_optimizer']
    data = dp.SameDayData((), {}, {777: ('C777', 'Նոր խանութ')}, {}, datetime(2026, 10, 1, 9, 0))
    with state.dispatch_lock:
        state.same_day_cache[date(2026, 10, 1)] = (time.monotonic(), data)
    [row] = _row(client, 777)
    assert (row['code'], row['name']) == ('C777', 'Նոր խանութ')
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 777, 'unload_min': 5}).status_code == 200

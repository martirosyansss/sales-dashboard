"""Время у магазина прямо в «Развозе» (ответ владельца №50): сохранение только времени — {"customer_id", "unload_min"}
без допуска и окна, своё время каждой точки дня в ответе «Развоза» (плашка), диалог «Ժամանակ խանութում» на странице."""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import ai_chat  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_dispatch_ai import FakeClient  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-10-01'
ACCESS = {'mode': 'deny', 'trucks': ['CAR1']}
WINDOW = {'kind': 'before', 't1': 720}
WINDOW_JSON = {'kind': 'before', 't1': 720, 't2': None, 'tol': None}
UNLOAD_ERROR = 'время у магазина — целое число минут от 1 до 120'


def _unload(client, cid, value):
    return client.post('/api/routes/customer-vehicles', json={'customer_id': cid, 'unload_min': value})


def _conditions(client, cid):
    """(допуск, окно, время у магазина) — как их видит «Условия магазина»."""
    row = client.get(f'/api/routes/customer-vehicles?customer_id={cid}').get_json()['customers'][0]
    return row['vehicle_access'], row['window'], row['unload_min']


def _day_stops(body):
    return {s['customer_id']: s for t in body['plan']['trucks'] for tr in t['trips'] for s in tr['stops']}


# ============================== хранение ==============================

def test_store_saves_only_unload_and_keeps_access_and_window(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_customer_constraints(101, VehicleAccess('deny', ('CAR1',)), st.CustomerWindow('before', 720), 'qa', 30)
    s.save_customer_unload(101, 40.0, 'logist')
    b = s.load()
    assert (b.vehicle_access[101], b.windows[101], b.unload_min) == \
        (VehicleAccess('deny', ('CAR1',)), st.CustomerWindow('before', 720), {101: 40.0})
    with closing(sqlite3.connect(s.path)) as conn:
        assert conn.execute('SELECT customer_id, fixed_min, updated_by FROM customer_unload').fetchall() == \
            [(101, 40.0, 'logist')]
        # допуск и окно не переписаны — у них прежний автор
        assert conn.execute('SELECT updated_by FROM customer_window').fetchall() == [('qa',)]
        assert conn.execute('SELECT updated_by FROM customer_vehicle_access').fetchall() == [('qa',)]
    s.save_customer_unload(101, None, 'logist')                                  # None — снова по норме
    b = s.load()
    assert b.unload_min == {} and 101 in b.windows and 101 in b.vehicle_access
    s.save_customer_unload(102, 25, 'logist')                                    # магазин без других условий
    assert s.load().unload_min == {102: 25.0}


@pytest.mark.parametrize('cid, value', [(101, 0), (101, 121), (101, 40.5), (101, True), (101, '40'),
                                        (0, 40), (True, 40), (2 ** 31, 40), ('101', 40)])
def test_store_unload_only_rejects_bad_input(tmp_path, cid, value):
    s = st.Store(str(tmp_path / 'r.db'))
    with pytest.raises(ValueError):
        s.save_customer_unload(cid, value, 'qa')
    assert s.load().unload_min == {}


def test_store_unload_only_failure_keeps_old_value_and_names_itself(tmp_path):
    """Сбой базы — StoreError со своим окончанием (страница переводит его по SERVER_HY_SUFFIX), старое время цело."""
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_customer_unload(101, 30, 'qa')
    with closing(sqlite3.connect(s.path)) as conn:
        conn.execute("CREATE TRIGGER reject_unload BEFORE UPDATE ON customer_unload "
                     "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.commit()
    with pytest.raises(st.StoreError, match=': не удалось сохранить время у магазина$'):
        s.save_customer_unload(101, 45, 'qa')
    assert s.load().unload_min == {101: 30.0}


# ============================== API: только время у магазина ==============================

def test_api_unload_only_does_not_overwrite_concurrent_conditions(client):
    """Логист открыл диалог в «Развозе», а в «Условиях магазина» тем временем сменили машины и окно: сохранение одного
    времени их не откатывает (форма с допуском и окном переслала бы то, что диалог видел при открытии)."""
    _dispatch_setup(client, [])
    assert _conditions(client, 103) == (None, None, None)                        # что видел диалог
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': ACCESS, 'window': WINDOW,
                                                              'unload_min': 30}).status_code == 200
    r = _unload(client, 103, 40)
    assert r.status_code == 200 and r.get_json() == {'success': True, 'customer_id': 103, 'unload_min': 40.0}
    assert _conditions(client, 103) == (ACCESS, WINDOW_JSON, 40.0)
    r = _unload(client, 103, None)                                               # null — снова обычное время
    assert r.status_code == 200 and r.get_json()['unload_min'] is None
    assert _conditions(client, 103) == (ACCESS, WINDOW_JSON, None)


@pytest.mark.parametrize('bad', [0, 121, -5, 40.5, True, False, '40', [], {}, 10 ** 400])
def test_api_unload_only_bad_value_is_400_and_saves_nothing(client, bad):
    _dispatch_setup(client, [])
    assert _unload(client, 103, 30).status_code == 200
    r = _unload(client, 103, bad)
    assert r.status_code == 400 and r.get_json()['errors'] == {'unload_min': UNLOAD_ERROR}
    assert client.application.extensions['route_optimizer'].store.load().unload_min == {103: 30.0}


@pytest.mark.parametrize('body, field', [
    ({'customer_id': 999, 'unload_min': 30}, 'customer_id'),             # нет в данных ERP раздела (новый магазин)
    ({'customer_id': True, 'unload_min': 30}, 'customer_id'),
    ({'customer_id': '103', 'unload_min': 30}, 'customer_id'),
    ({'customer_id': 0, 'unload_min': 30}, 'customer_id'),
    ({'customer_id': 103}, '_'),
    ({'customer_id': 103, 'window': None, 'unload_min': 30}, '_'),       # окно — только вместе с допуском
    ({'customer_id': 103, 'unload_min': 30, 'x': 1}, '_'),
])
def test_api_unload_only_bad_request_saves_nothing(client, body, field):
    _dispatch_setup(client, [])
    r = client.post('/api/routes/customer-vehicles', json=body)
    assert r.status_code == 400 and field in r.get_json()['errors']
    assert client.application.extensions['route_optimizer'].store.load().unload_min == {}


def test_api_full_shape_reports_access_before_unload(client):
    """Форма «Условий магазина»: при неверных и допуске, и времени — ошибка допуска, как до новой формы."""
    _dispatch_setup(client, [])
    r = client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': {'mode': 'bogus'},
                                                           'window': None, 'unload_min': 0})
    assert r.status_code == 400 and list(r.get_json()['errors']) == ['access']


# ============================== ответ «Развоза»: своё время у точки ==============================

def test_dispatch_day_carries_own_store_times(client):
    """store_unload — своё время у магазинов дня, где оно задано (клиент → мин); магазины не из этого дня — нет.
    План не меняется: unload_min точки рейса — как и было, разгрузка по плану (своё время + время на груз). Время
    сменили без пересборки — ответ дня сразу по сохранённому."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2),
                             _dorder(4, 999, 50.0)])
    assert _unload(client, 102, 40).status_code == 200 and _unload(client, 103, 50).status_code == 200
    assert client.get(f'/api/routes/dispatch?date={DAY}').get_json()['store_unload'] == {'102': 40.0}   # 103 — не в этом дне
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1']})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['store_unload'] == {'102': 40.0}
    stops = _day_stops(body)
    assert stops[102]['unload_min'] == pytest.approx(40 + 6 * 0.3, abs=0.05)    # своё время + 6 мин/т × 0,3 т
    assert stops[101]['unload_min'] == pytest.approx(8 + 6 * 0.4, abs=0.05)     # общая норма 8 мин
    assert all('store_unload' not in key for s in stops.values() for key in s)  # в plan — ничего нового
    assert _unload(client, 102, None).status_code == 200 and _unload(client, 101, 25).status_code == 200
    assert client.get(f'/api/routes/dispatch?date={DAY}').get_json()['store_unload'] == {'101': 25.0}
    assert _unload(client, 999, 30).status_code == 400                          # магазина нет в данных ERP раздела


def test_every_page_route_carries_store_times(client):
    """Страница перерисовывает день по ответу любой своей кнопки — store_unload в каждом (иначе плашки пропали бы)."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    assert _unload(client, 102, 40).status_code == 200
    body = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1']}).get_json()
    trip = body['plan']['trucks'][0]['trips'][0]['id']
    body = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': body['rev'], 'action': 'unpin',
                                                          'trip': trip}).get_json()
    assert body['success'] and body['store_unload'] == {'102': 40.0}
    body = client.post('/api/routes/dispatch/overtime', json={'date': DAY, 'rev': body['rev']}).get_json()
    assert body['success'] and body['store_unload'] == {'102': 40.0}
    body = client.post('/api/routes/dispatch/reset', json={'date': DAY}).get_json()
    assert body['success'] and body['store_unload'] == {'102': 40.0}


def test_ai_day_data_has_no_store_times(client, monkeypatch):
    """«Հարցրու AI-ին» видит день без store_unload (он только у страницы): ai_chat убирает customer_id у точек — номера
    клиентов модели ничего не скажут."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    assert client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1']}).status_code == 200
    assert _unload(client, 102, 40).status_code == 200
    assert client.get(f'/api/routes/dispatch?date={DAY}').get_json()['store_unload'] == {'102': 40.0}
    fake = FakeClient()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(ai_chat, '_get_client', lambda: fake)
    r = client.post('/api/routes/dispatch/ask', json={'date': DAY, 'question': 'Ո՞ր մեքենան', 'history': []})
    assert r.status_code == 200, r.get_json()
    day = fake.calls[0]['messages'][0]['content'][0]['text']
    assert day.startswith('<day_data') and 'Клиент 102' in day and 'store_unload' not in day


# ============================== страница ==============================

def test_page_dialog_and_translations(client):
    """Диалог «Ժամանակ խանութում» рядом с «Փոխել տեղը» (в конце #rtDispatch); страница пользуется этим API и полями
    ответа; нечисло в поле — ошибка, а не «пусто» (стёрло бы время молча); каждая ошибка сервера этой формы переведена
    (страница армянская, №31). Поведение диалога — в tests/routes_dispatch_browser_check.py (блок U)."""
    html = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    for piece in ('id="dpUnloadDlg"', 'id="dpUnloadMin"', 'id="dpUnloadSave"', 'id="dpUnloadClear"', 'id="dpUnloadCancel"',
                  'Ժամանակ խանութում', 'Հեռացնել'):
        assert piece in html, piece
    assert html.index('id="dpGeoDlg"') < html.index('id="dpUnloadDlg"') < html.index('{% endblock %}', html.index('id="dpGeoDlg"'))
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    for piece in ('/api/routes/customer-vehicles', 'unload_min', 'store_unload', 'unload_norms', 'unload_auto_min',
                  'unload_visits', 'validity.badInput', 'Բեռնաթափում՝ '):
        assert piece in js, piece
    _dispatch_setup(client, [])
    shape = client.post('/api/routes/customer-vehicles', json={'customer_id': 103}).get_json()['error']
    missing = client.post('/api/routes/customer-vehicles', json={'customer_id': 999, 'unload_min': 30}).get_json()['error']
    for text in (shape, missing, UNLOAD_ERROR):
        assert "'" + text + "'" in js, text
    assert "': не удалось сохранить время у магазина'" in js
    css = (ROOT / 'static' / 'css' / 'routes_dispatch.css').read_text(encoding='utf-8')
    assert '.dp-b-unload' in css and '.dp-unload-dlg' in css

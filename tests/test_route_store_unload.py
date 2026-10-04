"""Своё время разгрузки у каждого магазина (ответ владельца №50): хранение, API, «Развоз», обучение, страница."""
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import evaluate as evm  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_learning_loop import DEPOT, TODAY, A, B, _days, _learning_client, _unload_obs  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
NORMS = evm.Norms.from_settings(st.DEFAULT_SETTINGS)
TN = fl.TruckNorms(work_minutes=540.0, unload_min_per_stop=8.0, unload_min_per_tonne=6.0)
DAY = '2026-10-01'


def _minutes(body):
    return sum(tr['minutes'] for t in body['plan']['trucks'] for tr in t['trips'])


def _trip_of(body, cid):
    return next(tr for t in body['plan']['trucks'] for tr in t['trips'] if any(s['customer_id'] == cid
                                                                               for s in tr['stops']))


def _hhmm(text):
    return int(text[:2]) * 60 + int(text[3:5])


def _save(client, cid, unload, window=None, access=None):
    return client.post('/api/routes/customer-vehicles', json={'customer_id': cid, 'access': access, 'window': window,
                                                              'unload_min': unload})


def _build(client, trucks=('CAR1',)):
    """«Собрать рейсы» с чистого листа («Начать заново»: номера рейсов — с 1)."""
    assert client.post('/api/routes/dispatch/reset', json={'date': DAY}).status_code == 200
    r = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': list(trucks)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _plan(body):
    """План без отметки времени сборки."""
    return {k: v for k, v in body['plan'].items() if k != 'built_at'}


# ============================== проверка значения и хранение ==============================

@pytest.mark.parametrize('raw', [1, 40, 120, 40.0, 1.0])
def test_check_unload_min_accepts_whole_minutes(raw):
    assert st.check_unload_min(raw) == (float(raw), None)


@pytest.mark.parametrize('raw', [0, 121, -5, 40.5, 0.5, True, False, '40', None, float('nan'), float('inf'), [40], {}])
def test_check_unload_min_rejects(raw):
    value, err = st.check_unload_min(raw)
    assert value is None and 'от 1 до 120' in err


def test_store_roundtrip_keep_and_clear(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_customer_constraints(101, None, st.CustomerWindow('before', 720), 'qa', 40)
    assert s.load().unload_min == {101: 40.0}
    s.save_customer_constraints(101, VehicleAccess('deny', ('CAR1',)), None, 'qa')            # без поля — не трогает
    b = s.load()
    assert b.unload_min == {101: 40.0} and b.windows == {} and b.vehicle_access[101] == VehicleAccess('deny', ('CAR1',))
    s.save_customer_constraints(101, None, None, 'qa', 25.0)
    with closing(sqlite3.connect(s.path)) as conn:
        assert conn.execute('SELECT customer_id, fixed_min, updated_by FROM customer_unload').fetchall() == \
            [(101, 25.0, 'qa')]
    s.save_customer_constraints(101, None, None, 'qa', None)                                    # None — убрать
    assert s.load().unload_min == {}
    for bad in (0, 121, 40.5, True, '40'):
        with pytest.raises(ValueError):
            s.save_customer_constraints(101, None, None, 'qa', bad)


def test_store_rejects_broken_unload_rows(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.load()
    with closing(sqlite3.connect(s.path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):                                         # CHECK базы: 1–120
            conn.execute("INSERT INTO customer_unload VALUES(101, 0, 'x', 'qa')")
        conn.execute("INSERT INTO customer_unload VALUES(101, 40.5, 'x', 'qa')")             # не целое — битая строка
        conn.commit()
    with pytest.raises(st.StoreError, match='время у магазина'):
        s.load()


def test_store_unload_rolls_back_with_access_and_window(tmp_path):
    """Время у магазина пишется в той же транзакции, что допуск и окно: сбой на нём — не меняется ничего."""
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_customer_constraints(101, VehicleAccess('allow', ('CAR1',)), st.CustomerWindow('before', 720), 'qa', 30)
    with closing(sqlite3.connect(s.path)) as conn:
        conn.execute("CREATE TRIGGER reject_unload BEFORE UPDATE ON customer_unload "
                     "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.commit()
    with pytest.raises(st.StoreError):
        s.save_customer_constraints(101, VehicleAccess('deny', ('CAR2',)), st.CustomerWindow('at', 660, tol=0), 'qa', 45)
    b = s.load()
    assert (b.vehicle_access[101], b.windows[101], b.unload_min) == \
        (VehicleAccess('allow', ('CAR1',)), st.CustomerWindow('before', 720), {101: 30.0})


def test_store_migrates_14_to_15_keeps_all_rows(tmp_path):
    """14 → 15: только CREATE TABLE customer_unload — все прежние таблицы и строки как были."""
    path = str(tmp_path / 'v14.db')
    s = st.Store(path)
    s.save_customer_constraints(101, VehicleAccess('deny', ('CAR1',)), st.CustomerWindow('between', 600, 720), 'qa')
    s.save_geo_override(102, (40.2, 44.5), 'qa')
    s.save_dispatch(DAY, {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101]}]}, 'qa')
    s.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {'per_stop_min': 6.0, 'per_tonne_min': 9.0,
                                                                         'store_offsets': {'101': 55.0}})])
    s.save_learning_auto('loading', True, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE customer_unload')
        conn.execute("UPDATE meta SET value = '14' WHERE key = 'schema_version'")
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    b = st.Store(path).load()
    assert b.unload_min == {} and b.windows[101] == st.CustomerWindow('between', 600, 720)
    with closing(sqlite3.connect(path)) as conn:
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before} == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(st.SCHEMA_VERSION),)                                          # 14 → 15 → … → текущая
        assert conn.execute('SELECT COUNT(*) FROM customer_unload').fetchone() == (0,)
    st.Store(path).save_customer_constraints(101, None, None, 'qa', 40)
    assert st.Store(path).load().unload_min == {101: 40.0}


# ============================== API «Условия магазина» ==============================

def test_api_saves_searches_and_clears_unload(client):
    _dispatch_setup(client, [])
    state = client.application.extensions['route_optimizer']
    window = {'kind': 'before', 't1': 720}
    access = {'mode': 'deny', 'trucks': ['CAR1']}
    assert _save(client, 103, 40, window, access).status_code == 200
    row = client.get('/api/routes/customer-vehicles?q=C103').get_json()['customers'][0]
    assert (row['unload_min'], row['window']['kind'], row['vehicle_access']) == (40.0, 'before', access)
    # прежние формы запроса время у магазина не трогают
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': None}).status_code == 200
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': 103, 'access': None,
                                                              'window': None}).status_code == 200
    data = client.get('/api/routes/customer-vehicles').get_json()       # список без поиска — магазин с одним временем
    assert [(c['customer_id'], c['unload_min'], c['window'], c['vehicle_access']) for c in data['customers']] == \
        [(103, 40.0, None, None)]
    assert data['unload_norms'] == {'per_stop_min': 8.0, 'per_tonne_min': 6.0}
    assert _save(client, 103, None).status_code == 200                                     # null — обычное время
    assert state.store.load().unload_min == {}
    assert client.get('/api/routes/customer-vehicles').get_json()['customers'] == []


@pytest.mark.parametrize('bad', [0, 121, 40.5, True, '40', [], {}])
def test_api_bad_unload_saves_nothing(client, bad):
    _dispatch_setup(client, [])
    saved = {'kind': 'before', 't1': 720}
    assert _save(client, 103, 30, saved, {'mode': 'deny', 'trucks': ['CAR1']}).status_code == 200
    before = client.get('/api/routes/customer-vehicles?q=C103').get_json()
    r = _save(client, 103, bad, {'kind': 'at', 't1': 660, 'tol': 0}, None)
    assert r.status_code == 400 and 'unload_min' in r.get_json()['errors']
    assert client.get('/api/routes/customer-vehicles?q=C103').get_json() == before


def test_api_unload_needs_window_key_and_known_shape(client):
    _dispatch_setup(client, [])
    for body in ({'customer_id': 103, 'access': None, 'unload_min': 30},
                 {'customer_id': 103, 'access': None, 'window': None, 'unload_min': 30, 'x': 1}):
        assert client.post('/api/routes/customer-vehicles', json=body).status_code == 400
    assert client.application.extensions['route_optimizer'].store.load().unload_min == {}


def test_api_unload_norms_follow_learned_row_in_effect(client):
    _dispatch_setup(client, [])
    state = client.application.extensions['route_optimizer']
    state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 5.5, 'per_tonne_min': 9.0, 'store_offsets': {}})])
    assert client.get('/api/routes/customer-vehicles').get_json()['unload_norms'] == \
        {'per_stop_min': 5.5, 'per_tonne_min': 9.0}
    state.store.save_learning_auto('unload', False, 'qa')
    assert client.get('/api/routes/customer-vehicles').get_json()['unload_norms'] == \
        {'per_stop_min': 8.0, 'per_tonne_min': 6.0}


def test_settings_page_has_field_and_bumped_assets():
    html = (ROOT / 'templates' / 'routes_settings.html').read_text(encoding='utf-8')
    assert 'id="rcsUnload"' in html and 'Время у магазина, мин' in html
    assert 'routes_customer_settings.js\') }}?v=4' in html and 'routes_customer_settings.css\') }}?v=3' in html
    js = (ROOT / 'static' / 'js' / 'routes_customer_settings.js').read_text(encoding='utf-8')
    assert 'unload_min: unloadMin' in js and 'Время на сам груз (' in js and 'Пусто — ' in js
    assert 'input.validity.badInput' in js                  # нечисло в поле — ошибка, а не «пусто» (стёрло бы время)
    assert 'unload_auto_min' in js and 'unload_visits' in js and 'смешает с фактом' in js


def test_api_huge_unload_number_is_400(client):
    _dispatch_setup(client, [])
    r = _save(client, 103, 10 ** 400)
    assert r.status_code == 400 and 'unload_min' in r.get_json()['errors']
    assert client.application.extensions['route_optimizer'].store.load().unload_min == {}


# ============================== «Развоз»: применение ==============================

def test_manual_value_replaces_per_stop_part_of_one_stop():
    """Магазин 40 мин: разгрузка у него = 40 + 6 мин/т × т, у соседа — норма; рейс длиннее ровно на 40 − 8."""
    _, t2, _ = lr.apply_learned(NORMS, TN, {}, lr.InEffect(), {101: A, 102: B}, {101: 40.0})
    assert t2.unload_extra == {A: 32.0} and (t2.unload_min_per_stop, t2.unload_min_per_tonne) == (8.0, 6.0)
    assert t2.unload_at(1000.0, A) == pytest.approx(40 + 6) and t2.unload_at(500.0, B) == TN.unload(500.0)
    base = fl.route_trip([A, B], [1000.0, 500.0], DEPOT, NORMS, TN, reorder=False)
    got = fl.route_trip([A, B], [1000.0, 500.0], DEPOT, NORMS, t2, reorder=False)
    assert got[:2] == base[:2] and got[2] == pytest.approx(base[2] + 32.0)
    # пусто — норма: те же объекты, те же числа
    assert lr.apply_learned(NORMS, TN, {}, lr.InEffect(), {101: A}, {})[1] is TN
    assert lr.apply_learned(NORMS, TN, {}, lr.InEffect(), {101: A}, {999: 40.0})[1].unload_extra == {}


ROW = {'per_stop_min': 5.0, 'per_tonne_min': 10.0, 'store_offsets': {'102': 2.0, '104': 3.0},
       'store_stats': {'102': [10, 8.0]}}          # 102 — 10 визитов по 8 мин; 104 — поправка строки без факта


def test_store_times_blend_with_current_manual_at_apply():
    """Смесь — при применении, опора — введённое сейчас: 102 по факту 8 мин (10 визитов), введено 20 —
    (10·8 + 5·20) / 15 = 12; введено 50 — 22; пусто — (10·8 + 5·5) / 15 = 7 (опора — норма строки 5, ровно её
    поправка 2). Без факта: введённое (101), иначе поправка строки (104). Введённое — от нормы строки на точку."""
    def fixed(manual):
        return {c: 5.0 + e for c, e in lr.store_extras(5.0, manual, ROW).items()}
    assert fixed({101: 40.0, 102: 20.0}) == {101: 40.0, 102: 12.0, 104: 8.0}
    assert fixed({102: 50.0}) == {102: 22.0, 104: 8.0}
    assert fixed({}) == {102: 7.0, 104: 8.0}
    assert fixed({104: 30.0}) == {102: 7.0, 104: 30.0}                     # нет факта — введённое главнее
    assert lr.store_times(5.0, {101: 40.0, 102: 20.0}, ROW) == {101: (35.0, 'manual'), 102: (7.0, 'learned'),
                                                               104: (3.0, 'learned')}
    _, t2, _ = lr.apply_learned(NORMS, TN, {}, lr.InEffect(unload=ROW), {101: A, 102: B}, {101: 40.0, 102: 20.0})
    assert (t2.unload_min_per_stop, t2.unload_extra) == (5.0, {A: 35.0, B: 7.0})
    assert t2.unload_at(0.0, A) == 40.0 and t2.unload_at(0.0, B) == 12.0


def test_store_times_without_manual_equal_learned_offset_and_bad_stats_fall_back():
    """Без введённого — та же поправка, что хранит строка (в пределах округления; меньше 0,5 мин — ноль, как раньше);
    битая запись store_stats строку не портит — у её магазина правило без факта."""
    obs = _unload_obs(store_extra={101: 10.0, 102: -1.0, 103: 0.3}, noise=0.5)
    p = lr.fit_unload(obs, lambda x: 8 * x.n + 6 * x.tonnes, TODAY).params
    extras = lr.store_extras(p['per_stop_min'], {}, p)
    assert {str(c): e for c, e in extras.items() if e} == p['store_offsets']
    assert extras[103] == 0.0 and '103' not in p['store_offsets']
    a, b = p['per_stop_min'], p['per_tonne_min']
    train = [o for o in obs if o.day < TODAY - timedelta(days=lr.HOLDOUT_DAYS)]
    for c in (101, 102):                     # поправка, которую хранила строка до №50: med·n / (n + 5) в [−a, 60]
        rs = [o.minutes - a - b * o.tonnes for o in train if o.customers == (c,)]
        assert extras[c] == pytest.approx(round(max(-a, min(60.0, median(rs) * len(rs) / (len(rs) + 5))), 1), abs=0.1)
    bad = {**p, 'store_stats': {**p['store_stats'], '101': [0, 'x'], 'x': [5, 5.0]}}
    assert lr.valid_params('unload', bad)
    assert lr.store_extras(p['per_stop_min'], {}, bad) == extras                      # 101 — поправка строки
    assert lr.store_extras(p['per_stop_min'], {101: 30.0}, bad)[101] == 30.0 - p['per_stop_min']
    assert lr.store_extras(5.0, {}, {**ROW, 'store_stats': 'oops'}) == {102: 2.0, 104: 3.0}


def test_unload_never_negative_and_shared_point_takes_mean():
    old = {'per_stop_min': 8.0, 'per_tonne_min': 6.0, 'store_offsets': {'102': -120.0}}
    assert lr.store_extras(8.0, {101: 1.0}, old) == {101: -7.0, 102: -8.0}
    tn = replace(TN, unload_extra=lr.unload_extra(8.0, {101: 1.0}, old, {101: A, 102: B}))
    assert tn.unload_at(0.0, A) == 1.0 and tn.unload_at(0.0, B) == 0.0 and tn.unload_at(1000.0, B) == 6.0
    assert lr.store_times(8.0, {}, {'store_stats': {'1': [10, -100.0]}}) == {1: (-8.0, 'learned')}   # по факту < 0
    # клиенты дня в одной точке: у каждой стоянки — среднее их поправок (без своего времени — 0): в сумме — своё время
    # каждого магазина, соседу время не переносится; порядок клиентов не важен
    for points in ({101: A, 102: A, 103: B}, {103: B, 102: A, 101: A}):
        assert lr.unload_extra(8.0, {101: 40.0, 102: 20.0}, None, points) == {A: 22.0}
        assert lr.unload_extra(8.0, {101: 40.0}, None, points) == {A: 16.0}
    assert lr.unload_extra(8.0, {101: 40.0}, None, {102: A}) == {}


def test_shared_point_split_across_trips_gets_mean_documented_undercount():
    """Принятое ограничение (unload_extra, store-unload-plan.md): магазины одной точки в разных рейсах — у каждого рейса
    среднее. 101 (40 мин) один в рейсе — 8 + 16 = 24, а не 40; 102 (без своего времени) — тоже 24, а не 8; за день —
    те же 48 (как у каждого своё)."""
    tn = replace(TN, unload_extra=lr.unload_extra(8.0, {101: 40.0}, None, {101: A, 102: A}))
    base = fl.route_trip([A], [0.0], DEPOT, NORMS, TN, reorder=False)[2]
    trip_101 = fl.route_trip([A], [0.0], DEPOT, NORMS, tn, reorder=False)[2]
    trip_102 = fl.route_trip([A], [0.0], DEPOT, NORMS, tn, reorder=False)[2]
    assert trip_101 == pytest.approx(base + 16.0) and trip_102 == pytest.approx(base + 16.0)   # не +32 и не +0
    assert (trip_101 - base) + (trip_102 - base) == pytest.approx(32.0)                        # день — как у каждого своё


def test_shared_point_fleet_equals_learning_prediction():
    """101 (40 мин) и 102 (без своего времени) в одной точке: разгрузка в точке по fleet — ровно прогноз обучения для
    стоянки с обоими (Σ по клиентам), и с выученной строкой тоже."""
    for manual, unload in (({101: 40.0}, None), ({101: 40.0, 104: 12.0}, ROW)):
        per_stop = unload['per_stop_min'] if unload else 8.0
        per_tonne = unload['per_tonne_min'] if unload else 6.0
        tn = fl.TruckNorms(540.0, per_stop, per_tonne,
                           unload_extra=lr.unload_extra(per_stop, manual, unload, {101: A, 102: A, 104: B}))
        fleet_total = tn.unload_at(300.0, A) + tn.unload_at(500.0, A)
        extras = lr.store_extras(per_stop, manual, unload)
        obs = lr.UnloadObs(TODAY, 2, 0.8, 0.0, (101, 102))
        predicted = per_stop * obs.n + per_tonne * obs.tonnes + math.fsum(extras.get(c, 0.0) for c in obs.customers)
        assert fleet_total == pytest.approx(predicted)
    assert fleet_total == pytest.approx(5 * 2 + 10 * 0.8 + 35 + 2)    # 101: 40 − 5; 102: по факту (опора 5) +2


def test_heavy_order_pays_store_time_on_every_trip():
    trucks = [fl.FleetTruck('T1', 'HOWO', 5000.0, 28.0)]
    pts, kgs, revs = [A, B], [9000.0, 300.0], [9000.0 * 120, 300.0 * 120]          # A — две поездки
    _, t2, _ = lr.apply_learned(NORMS, TN, {}, lr.InEffect(), {101: A}, {101: 30.0})
    plain = fl.route_day(pts, kgs, revs, DEPOT, trucks, NORMS, TN)
    got = fl.route_day(pts, kgs, revs, DEPOT, trucks, NORMS, t2)
    visits = sum(1 for t in plain for i in t.items if i == 0)
    assert visits >= 2 and [t.items for t in got] == [t.items for t in plain]
    assert sum(t.minutes for t in got) == pytest.approx(sum(t.minutes for t in plain) + 22.0 * visits)


def test_build_with_store_time_and_empty_restores_plan(client):
    """Настоящий путь «Собрать рейсы»: 40 мин у 101 — рейс на 32 мин длиннее и возвращается позже на столько же;
    пусто — план байт-в-байт как без значения."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    base = _build(client)
    assert _save(client, 101, 40).status_code == 200
    got = _build(client)
    assert _minutes(got) == pytest.approx(_minutes(base) + 32, abs=1)
    assert [s['customer_id'] for s in _trip_of(got, 101)['stops']] == [s['customer_id'] for s in _trip_of(base, 101)['stops']]
    assert _hhmm(_trip_of(got, 101)['return']) - _hhmm(_trip_of(base, 101)['return']) == pytest.approx(32, abs=1)
    assert _save(client, 101, None).status_code == 200
    assert _plan(_build(client)) == _plan(base)


def test_store_time_applies_without_learning_and_with_switch_off(client):
    """Введённое действует всегда: нет выученных строк; строка есть, но автообучение разгрузки выключено (тогда и её
    поправки магазинов не действуют); строка действует — у её магазина выученное, у другого введённое от её нормы."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    assert _save(client, 101, 40).status_code == 200 and _save(client, 102, 20).status_code == 200
    snap, _ = state.snapshots.cached()
    bundle = views._bundle(state)
    ready = views._ready_trucks(snap, bundle)
    pts = {101: (40.18, 44.50), 102: (40.19, 44.52)}

    def extra(**kw):
        ctx = views._dispatch_ctx(state, snap, views._bundle(state), date(2026, 10, 1), ready, list(pts.values()),
                                  pts, **kw)
        return ctx.tn.unload_min_per_stop, dict(ctx.tn.unload_extra)
    assert extra() == (8.0, {pts[101]: 32.0, pts[102]: 12.0})
    assert extra(learned=False) == (8.0, {pts[101]: 32.0, pts[102]: 12.0})
    # строка до №50 (поправка без факта): введённое главнее, от нормы строки на точку
    state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 5.0, 'per_tonne_min': 10.0, 'store_offsets': {'102': 3.0}})])
    assert extra() == (5.0, {pts[101]: 35.0, pts[102]: 15.0})
    # строка с фактом у 102: смесь с введённым сейчас — 20 → (10·8 + 5·20) / 15 = 12; 60 → 25,3; пусто → 7
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', ROW)])
    assert extra() == (5.0, {pts[101]: 35.0, pts[102]: 7.0})
    assert _save(client, 102, 60).status_code == 200
    assert extra() == (5.0, {pts[101]: 35.0, pts[102]: 20.3})
    assert _save(client, 102, None).status_code == 200
    assert extra() == (5.0, {pts[101]: 35.0, pts[102]: 2.0})
    state.store.save_learning_auto('unload', False, 'qa')
    assert extra() == (8.0, {pts[101]: 32.0})


def test_store_time_reaches_plan_vs_fact_and_survives_broken_journal(client, monkeypatch):
    from route_optimizer.dispatch import ShippedDoc
    _dispatch_setup(client, [_dorder(1, 101, 400.0)], docs=[ShippedDoc(101, 1, 'CAR1', 1000.0, 300.0)])
    state = client.application.extensions['route_optimizer']
    assert _save(client, 101, 40).status_code == 200
    seen = []
    real = lr.apply_learned
    monkeypatch.setattr(lr, 'apply_learned', lambda *a: seen.append(a[3:6]) or real(*a))
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    assert client.get('/api/routes/dispatch/fact?date=2026-10-01').status_code == 200
    assert seen and seen[-1][2] == {101: 40.0} and 101 in seen[-1][1]
    with closing(sqlite3.connect(state.store.path)) as conn:              # журнал битый — введённое всё равно
        conn.execute("INSERT INTO learned_norms(kind, scope, run_day, params, n_obs, n_test, accepted, reason, "
                     "created_at) VALUES('unload', '', '2026-09-01', '{oops', 1, 1, 1, 'x', 'x')")
        conn.commit()
    seen.clear()
    assert client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1']}).status_code == 200
    assert state.learning_warning and seen and seen[-1][0] == lr.InEffect() and seen[-1][2] == {101: 40.0}


# ============================== без введённых значений — как до №50 ==============================

# 14 магазинов (ручные точки), две машины, сборка «Собрать рейсы» через API: отпечаток плана, посчитанный кодом
# feature/route-optimizer 84f8bb1 (до №50; на 8a4cce2 — f508b0a0…/811e8759…, «почему так» (№49) добавило в план
# пояснение) этим же тестом. Нет введённых значений и выученных строк — план байт-в-байт тот же; с выученной
# строкой (поправки магазинов в разных точках) — тоже: путь выученного не изменился.
GOLDEN_POINTS = {200 + i: (round(40.150 + 0.011 * (i % 5), 4), round(44.480 + 0.017 * (i // 5) + 0.003 * i, 4))
                 for i in range(14)}
GOLDEN_KG = {cid: 150.0 + 97.0 * ((cid * 7) % 11) for cid in GOLDEN_POINTS}
GOLDEN = {'plain': '76d4e659d316cd041e3e620c3b2aa78681af666e2a614a2c27f4bac0834c8594',
          'learned': '8e9e79562a36566c6fc5361c77cf2c9cd8cdf29ae95abf4f4170446c2351847e'}


def _golden_digest(client, learned, manual=None):
    _dispatch_setup(client, [_dorder(i + 1, cid, GOLDEN_KG[cid], agent=1 + i % 2, rev=20000.0 + 1000 * i)
                             for i, cid in enumerate(sorted(GOLDEN_POINTS))])
    state = client.application.extensions['route_optimizer']
    for cid, p in GOLDEN_POINTS.items():
        state.store.save_geo_override(cid, p, 'qa')
    for cid, minutes in (manual or {}).items():
        state.store.save_customer_constraints(cid, None, None, 'qa', minutes)
    if learned:
        state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {
            'per_stop_min': 6.5, 'per_tonne_min': 9.0, 'store_offsets': {'201': 7.5, '205': -2.0, '212': 12.0}})])
    plan = _plan(_build(client, ('CAR1', 'CAR2')))
    assert plan['coverage']['stops_assigned'] == len(GOLDEN_POINTS)
    # совет «что добавить» (№54, plan.advice) — новое поле плана, не рейсы: здесь всё помещается, совета нет; отпечаток —
    # по рейсам и цифрам, как до него
    assert plan.pop('advice', None) is None
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


@pytest.mark.parametrize('learned', [False, True])
def test_no_store_time_plans_identical_to_base(client, learned):
    assert _golden_digest(client, learned) == GOLDEN['learned' if learned else 'plain']


def test_golden_digest_sees_store_time(client):
    """Отпечаток чувствителен ко времени у магазина: одно введённое значение — другой план."""
    assert _golden_digest(client, False, {203: 45}) != GOLDEN['plain']


# ============================== обучение (fit_unload) ==============================

def _exact_obs(store_extra=None, days=40):
    """Разгрузка без шума: 4 мин + 12 мин/т, у магазинов из store_extra — своё время 4 + поправка."""
    return _unload_obs(store_extra=store_extra, noise=0.0, days=days)


def _train_visits(obs, cid):
    return sum(1 for o in obs if o.customers == (cid,) and o.day < TODAY - timedelta(days=lr.HOLDOUT_DAYS))


def test_fit_unload_prior_pulls_store_time_toward_manual():
    """Своё время по факту 14 (a 4 + 10); введено 60 — в строку смесь (n·14 + 5·60) / (n + 5), без введённого —
    прежняя поправка 10·n / (n + 5); другие магазины — как без введённого."""
    obs = _exact_obs({101: 10.0})
    cur = lambda x: 8 * x.n + 6 * x.tonnes   # noqa: E731
    plain = lr.fit_unload(obs, cur, TODAY)
    pulled = lr.fit_unload(obs, cur, TODAY, {101: 60.0})
    n = _train_visits(obs, 101)
    a = pulled.params['per_stop_min']
    assert a == pytest.approx(4, abs=0.05) and plain.params['per_stop_min'] == a
    assert plain.params['store_offsets']['101'] == pytest.approx(10 * n / (n + 5), abs=0.06)
    assert a + pulled.params['store_offsets']['101'] == pytest.approx((n * 14 + 5 * 60) / (n + 5), abs=0.06)
    assert {k: v for k, v in pulled.params['store_offsets'].items() if k != '101'} == \
        {k: v for k, v in plain.params['store_offsets'].items() if k != '101'}
    assert pulled.params['store_stats']['101'] == [n, pytest.approx(14, abs=0.05)]
    # введённое совпадает с фактом — поправка хранится и при |поправка| < 0,5 (иначе действовало бы введённое)
    same = lr.fit_unload(_exact_obs(), cur, TODAY, {102: 4.2})
    assert same.params['store_offsets']['102'] == pytest.approx(0.0, abs=0.06)
    assert '102' not in lr.fit_unload(_exact_obs(), cur, TODAY).params['store_offsets']
    assert lr.valid_params('unload', pulled.params) and lr.valid_params('unload', json.loads(json.dumps(pulled.params)))


def test_fit_unload_store_time_bounded_0_to_120():
    obs = _exact_obs({101: 10.0})
    cur = lambda x: 8 * x.n + 6 * x.tonnes   # noqa: E731
    p = lr.fit_unload(obs, cur, TODAY, {101: 120.0}).params
    assert 0 <= p['per_stop_min'] + p['store_offsets']['101'] <= lr.STORE_OFFSET_MAX


def test_fit_unload_few_visits_manual_applies_in_check():
    """Магазин 105 (своё время 25 мин) — 1 визит в обучении: в строке его нет, но проверка считает его введённым
    временем — ровно тем, что применится (ошибка у его визитов — 0); store_stats показывает его визит."""
    test_from = TODAY - timedelta(days=lr.HOLDOUT_DAYS)
    obs = [o for o in _exact_obs() if o.customers != (105,)]
    obs += [lr.UnloadObs(d, 1, 0.5, 25 + 12 * 0.5, (105,)) for d in _days(1, start=test_from - timedelta(days=10))]
    held = [lr.UnloadObs(d, 1, 0.5, 25 + 12 * 0.5, (105,)) for d in _days(7, start=test_from)]
    obs += held
    cur = lambda x: 8 * x.n + 6 * x.tonnes   # noqa: E731
    without, with_manual = lr.fit_unload(obs, cur, TODAY), lr.fit_unload(obs, cur, TODAY, {105: 25.0})
    assert '105' not in with_manual.params['store_offsets'] and with_manual.params['store_stats']['105'][0] == 1
    assert '105' not in without.params['store_stats'] and with_manual.n_test == without.n_test
    p = with_manual.params
    assert p['per_stop_min'] == without.params['per_stop_min']
    # у визитов 105 прогноз — 25 + b·т (введённое), без введённого — a + b·т: разница ошибок — ровно их вклад
    gap = len(held) * abs(25.0 - p['per_stop_min']) / with_manual.n_test
    assert without.mae_after - with_manual.mae_after == pytest.approx(gap, abs=0.002)


def test_valid_params_old_rows_and_store_stats():
    old = {'per_stop_min': 8, 'per_tonne_min': 6, 'store_offsets': {'101': 55.0, '102': -8.0}}   # строка до №50
    assert lr.valid_params('unload', old) and lr.in_effect([{'kind': 'unload', 'scope': '', 'accepted': 1,
                                                            'params': old, 'model_id': None}], {}, None).unload == old
    assert lr.valid_params('unload', {**old, 'store_offsets': {'101': 110.0}})
    assert not lr.valid_params('unload', {**old, 'store_offsets': {'101': 121.0}})
    good = {**old, 'store_stats': {'101': [7, 12.5], '105': [1, -3.0]}}
    assert lr.valid_params('unload', good) and lr.store_stats(good) == {101: (7, 12.5), 105: (1, -3.0)}
    # битые записи store_stats строку не портят (они только у своего магазина) — пропускаются
    for bad in ({'101': [0, 5.0]}, {'101': [True, 5.0]}, {'101': [2.0, 5.0]}, {'101': [2, -121.0]}, {'101': [2, 91.0]},
                {'101': [2, 5.0, 1]}, {'x': [2, 5.0]}, [['101', 2, 5.0]], 'oops', {'101': [2, float('nan')]}):
        assert lr.valid_params('unload', {**old, 'store_stats': bad}), bad
        assert lr.store_stats({**old, 'store_stats': bad}) == {}, bad


def test_run_learning_compares_against_current_with_manual(client, monkeypatch):
    """Ночной прогон: «действующая норма» для сравнения и опора — с введённым временем магазинов (нет строки —
    от нормы на точку из настроек; строка действует — у её магазинов выученное, у остальных введённое от её нормы)."""
    state = _learning_client(client, monkeypatch)
    state.store.save_customer_constraints(101, None, None, 'qa', 30)
    seen = []
    real = lr.fit_unload
    monkeypatch.setattr(lr, 'fit_unload', lambda obs, cur, today, manual=None, plain=None:
                        seen.append((cur, manual, plain)) or real(obs, cur, today, manual, plain))
    out = {o.kind: o for o in views.run_learning(state, TODAY)}
    cur, manual, plain = seen[-1]
    assert manual == {101: 30.0}
    assert cur(lr.UnloadObs(TODAY, 1, 0.5, 0.0, (101,))) == pytest.approx(30 + 6 * 0.5)
    assert plain(lr.UnloadObs(TODAY, 1, 0.5, 0.0, (101,))) == pytest.approx(8 + 6 * 0.5)   # отсечение — не строже нормы
    assert cur(lr.UnloadObs(TODAY, 1, 0.5, 0.0, (102,))) == pytest.approx(8 + 6 * 0.5)
    p = out['unload'].params
    a, n = p['per_stop_min'], p['store_stats']['101'][0]
    fact = p['store_stats']['101'][1]
    assert out['unload'].accepted and a == pytest.approx(4, abs=0.6) and n >= lr.STORE_MIN_OBS
    assert a + p['store_offsets']['101'] == pytest.approx((n * fact + 5 * 30) / (n + 5), abs=0.2)
    # следующий день: строка действует — 101 по ней, 102 введённое (20) от её нормы на точку
    state.store.save_customer_constraints(102, None, None, 'qa', 20)
    views.run_learning(state, TODAY + timedelta(days=1))
    cur, manual, _ = seen[-1]
    assert manual == {101: 30.0, 102: 20.0}
    assert cur(lr.UnloadObs(TODAY, 1, 0.0, 0.0, (101,))) == pytest.approx(a + p['store_offsets']['101'])
    # 102 с фактом (≥ 2 визитов): смесь с введённым 20 — ровно та, что применит «Развоз»
    n2, fact2 = p['store_stats']['102']
    blend = round((n2 * fact2 + 5 * 20) / (n2 + 5) - a, 1)
    assert cur(lr.UnloadObs(TODAY, 1, 0.0, 0.0, (102,))) == pytest.approx(a + blend)
    assert a + lr.store_extras(a, manual, p)[102] == pytest.approx(a + blend)


def test_learning_page_store_table(client, monkeypatch):
    state = _learning_client(client, monkeypatch)
    state.store.save_customer_constraints(101, None, None, 'qa', 30)
    state.store.save_customer_constraints(103, None, None, 'qa', 15)                  # визитов по факту нет
    status = client.get('/api/routes/learning/status').get_json()['status']
    st0 = next(s for s in status if s['kind'] == 'unload')['stores']
    assert [(r['customer_id'], r['manual_min'], r['visits'], r['in_calc_min'], r['source']) for r in st0['rows']] == \
        [(101, 30.0, None, 30.0, 'manual'), (103, 15.0, None, 15.0, 'manual')]
    assert (st0['per_stop_min'], st0['per_tonne_min'], st0['run_day']) == (8.0, 6.0, None)
    views.run_learning(state, TODAY)
    d = client.get('/api/routes/learning').get_json()
    stores = next(s for s in d['status'] if s['kind'] == 'unload')['stores']
    rows = {r['customer_id']: r for r in stores['rows']}
    p = next(r['params'] for r in reversed(state.store.learned()) if r['kind'] == 'unload')
    assert stores['run_day'] == TODAY.isoformat() and stores['per_stop_min'] == p['per_stop_min']
    assert rows[101]['source'] == 'learned' and rows[101]['visits'] == p['store_stats']['101'][0]
    assert rows[101]['in_calc_min'] == pytest.approx(p['per_stop_min'] + p['store_offsets']['101'], abs=0.06)
    assert rows[103]['source'] == 'manual' and rows[103]['in_calc_min'] == 15.0 and rows[103]['visits'] is None
    assert (rows[101]['name'], rows[101]['code']) == ('Клиент 101', 'C101')
    visits = [r['visits'] or 0 for r in stores['rows']]
    assert visits == sorted(visits, reverse=True) and stores['total'] == len(stores['rows']) == stores['shown']
    client.post('/api/routes/learning/auto', json={'kind': 'unload', 'auto': False})
    rows = {r['customer_id']: r for r in next(s for s in client.get('/api/routes/learning/status').get_json()['status']
                                              if s['kind'] == 'unload')['stores']['rows']}
    assert (rows[101]['source'], rows[101]['in_calc_min']) == ('manual', 30.0)        # выключено — введённое


def test_settings_hint_is_truthful_with_learned_row(client):
    """Подсказка «Условий магазина» — то, что посчитает «Развоз»: пустое поле у магазина с фактом — его смесь с нормой
    строки (а не «обычные M мин»), разгрузок по факту — введённое смешается; у магазина без факта — обычная норма."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', ROW)])
    assert _save(client, 102, 60).status_code == 200
    data = client.get('/api/routes/customer-vehicles?q=C10').get_json()
    rows = {c['customer_id']: c for c in data['customers']}
    assert data['unload_norms'] == {'per_stop_min': 5.0, 'per_tonne_min': 10.0}
    assert (rows[102]['unload_min'], rows[102]['unload_auto_min'], rows[102]['unload_visits']) == (60.0, 7.0, 10)
    assert (rows[101]['unload_min'], rows[101]['unload_auto_min'], rows[101]['unload_visits']) == (None, 5.0, None)
    assert _save(client, 102, None).status_code == 200                     # убрали — «Развоз» берёт ровно подсказку
    snap, _ = state.snapshots.cached()
    pts = {102: (40.19, 44.52)}
    ctx = views._dispatch_ctx(state, snap, views._bundle(state), date(2026, 10, 1),
                              views._ready_trucks(snap, views._bundle(state)), list(pts.values()), pts)
    assert ctx.tn.unload_at(0.0, pts[102]) == pytest.approx(rows[102]['unload_auto_min'])


def _js_hints(norms, items):
    """unloadHint из routes_customer_settings.js — в node, на ответе сервера (unload_norms и строки магазинов)."""
    js = (ROOT / 'static' / 'js' / 'routes_customer_settings.js').read_text(encoding='utf-8').replace('\r\n', '\n')
    parts = [re.search(p, js, re.S).group(0) for p in (r'    const minutes = .*?;\n', r'    const unloads = .*?;\n',
                                                         r'    function unloadHint\(item\) \{\n.*?\n    \}\n')]
    script = f'const norms = {json.dumps(norms)};\n' + ''.join(parts) + \
        f'console.log(JSON.stringify({json.dumps(items)}.map(unloadHint)));\n'
    out = subprocess.run(['node'], input=script, capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='нет node')
def test_settings_hint_rounded_learned_norm_is_not_by_fact(client):
    """Норма обучения — до сотых (8,37), время в подсказке сервер шлёт до десятых (8,4): у магазина без своего времени
    это округление — «обычные», а не «по факту»; 8,75 → 8,8 — тоже (разница ровно 0,05). Своё время по факту — «по
    факту»."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    for run_day, per_stop in (('2026-09-02', 8.37), ('2026-09-03', 8.75)):
        state.store.save_learned(run_day, [lr.Outcome('unload', '', True, 'да', {
            'per_stop_min': per_stop, 'per_tonne_min': 6.0, 'store_offsets': {}, 'store_stats': {'102': [10, 12.4]}})])
        data = client.get('/api/routes/customer-vehicles?q=C10').get_json()
        rows = {c['customer_id']: c for c in data['customers']}
        assert data['unload_norms']['per_stop_min'] == per_stop and rows[101]['unload_auto_min'] == round(per_stop, 1)
        assert rows[101]['unload_auto_min'] != per_stop and rows[102]['unload_auto_min'] - per_stop > 1
        plain, fact = _js_hints(data['unload_norms'], [rows[101], rows[102]])
        assert plain.endswith(' Пусто — обычные ' + f'{per_stop:.1f}'.replace('.', ',') + ' мин.'), plain
        assert fact.endswith(' мин — по факту.') and 'обычные' not in fact, fact
    assert _js_hints({'per_stop_min': 8.0, 'per_tonne_min': 6.0}, [{'unload_auto_min': 8.5}])[0].endswith(
        ' Пусто — 8,5 мин — по факту.')                                  # поправка 0,5 — уже своё время


def test_manual_below_real_time_is_corrected_by_fact():
    """Введено 5 мин, на деле 25: отсечение «дольше 3 × нормы» — не строже общей нормы, визиты магазина остаются в
    обучении и проверке, смесь сдвигает время магазина к 25."""
    test_from = TODAY - timedelta(days=lr.HOLDOUT_DAYS)
    obs = [o for o in _unload_obs(noise=0.0, days=40) if o.customers != (105,)]
    obs += [lr.UnloadObs(d, 1, 0.3, 25 + 12 * 0.3, (105,)) for d in _days(12, start=test_from - timedelta(days=20))]
    obs += [lr.UnloadObs(d, 1, 0.3, 25 + 12 * 0.3, (105,)) for d in _days(6, start=test_from)]
    plain = lambda o: 8 * o.n + 6 * o.tonnes   # noqa: E731
    ext = lr.store_extras(8.0, {105: 5.0}, None)
    cur = lambda o: plain(o) + math.fsum(ext.get(c, 0.0) for c in o.customers)   # noqa: E731
    without = lr.fit_unload(obs, plain, TODAY)
    out = lr.fit_unload(obs, cur, TODAY, {105: 5.0}, plain)
    assert out.n_test == without.n_test and out.n_obs == without.n_obs
    p = out.params
    assert p['store_stats']['105'] == [12, 25.0]
    fixed = p['per_stop_min'] + lr.store_extras(p['per_stop_min'], {105: 5.0}, p)[105]
    assert fixed == pytest.approx((12 * 25 + 5 * 5) / 17, abs=0.1) and fixed > 15
    no_plain = lr.fit_unload(obs, cur, TODAY, {105: 5.0})                  # без plain отсечение выбросило бы 105
    assert no_plain.params['store_stats'].get('105') is None


def test_learning_page_lists_active_offsets_without_stats_and_never_loads_erp(client, monkeypatch):
    """Строка до №50 действует, последний пересчёт не принят: её магазины со своим временем — в таблице (как в
    «своё время у N магазинов»). Опрос статуса после перезапуска (снимка в памяти нет) ERP не читает — без названий."""
    state = _learning_client(client, monkeypatch)
    state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 5.0, 'per_tonne_min': 10.0, 'store_offsets': {'104': 6.0, '102': -1.5}})])
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', False, 'не лучше', {
        'per_stop_min': 4.0, 'per_tonne_min': 12.0, 'store_offsets': {}, 'store_stats': {'101': [9, 4.1]}})])
    calls = []
    state.snapshots = SnapshotCache(lambda: calls.append(1) or (_ for _ in ()).throw(AssertionError('ERP')))
    status = client.get('/api/routes/learning/status').get_json()['status']
    stores = next(s for s in status if s['kind'] == 'unload')['stores']
    rows = {r['customer_id']: r for r in stores['rows']}
    assert calls == [] and set(rows) == {101, 102, 104} and all(r['name'] is None for r in rows.values())
    assert (rows[104]['in_calc_min'], rows[104]['source'], rows[104]['visits']) == (11.0, 'learned', None)
    assert (rows[102]['in_calc_min'], rows[101]['in_calc_min'], rows[101]['visits']) == (3.5, 5.0, 9)
    assert stores['per_stop_min'] == 5.0 and stores['run_day'] == '2026-09-02'


def test_learning_page_renders_store_block():
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    assert 'Разгрузка по магазинам' in html and 'id="lrStoreRows"' in html and "routes_learning.js') }}?v=8" in html
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert 'function renderStores' in js and "esc(r.name)" in js and "kind === 'unload'" in js


# ============================== со 2-го визита (выбор владельца к №50) ==============================

def test_store_times_one_visit_ignored_two_visits_weight_two_sevenths():
    """Один визит — не в счёт: введённое (102), нет — норма (101 нет в ответе). Два — смесь, вес факта ровно 2/7.
    Норма строки 6, факт 41: без введённого 6 + 2/7·35 = 16 (103), введено 20 — 20 + 2/7·21 = 26 (104)."""
    row = {'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {},
           'store_stats': {'101': [1, 41.0], '102': [1, 41.0], '103': [2, 41.0], '104': [2, 41.0]}}
    assert lr.store_times(6.0, {102: 20.0, 104: 20.0}, row) == {102: (14.0, 'manual'), 103: (10.0, 'learned'),
                                                               104: (20.0, 'learned')}
    # вес факта n / (n + 5): 0 у одного визита, 2/7 у двух и дальше больше с каждым визитом — и к введённому, и к норме
    for prior, manual in ((6.0, {}), (20.0, {1: 20.0})):
        fact, weights = 76.0, []
        for n in range(1, 16):
            extra, _ = lr.store_times(6.0, manual, {'store_stats': {'1': [n, fact]}}).get(1, (0.0, 'norm'))
            weights.append((6.0 + extra - prior) / (fact - prior))
        assert weights == pytest.approx([0.0] + [n / (n + 5) for n in range(2, 16)], abs=0.05 / (fact - prior))


def test_store_times_from_5_visits_same_as_before(monkeypatch):
    """Магазины с ≥ 5 визитами — ровно как при прежнем пороге 5 при любом введённом; меняются только 2–4 визита
    (раньше у них — введённое или норма)."""
    row = {'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {'130': 3.0},
           'store_stats': {str(100 + n): [n, 30.0 + n] for n in range(1, 21)}}
    for manual in ({}, {c: 15.0 for c in range(101, 121)}, {103: 60.0, 107: 1.0, 130: 9.0}):
        new = lr.store_times(6.0, manual, row)
        with monkeypatch.context() as m:
            m.setattr(lr, 'STORE_MIN_OBS', 5)
            old = lr.store_times(6.0, manual, row)
        assert set(new) == set(old) | {102, 103, 104}
        assert {c for c in new if new[c] != old.get(c)} == {102, 103, 104}, manual


def _two_visits(cid, minutes, tonnes=0.5):
    """Визиты магазина в обучении (с 10-го дня до отложенной недели), по одному на день."""
    start = TODAY - timedelta(days=lr.HOLDOUT_DAYS + 10)
    return [lr.UnloadObs(d, 1, tonnes, m, (cid,)) for d, m in zip(_days(len(minutes), start=start), minutes)]


def test_fit_unload_learns_from_second_visit_with_weight_two_sevenths():
    """Своё время 25 мин. 201 — один визит в обучении: не в счёт (введённое, нет — норма). 202 — два: в строке, вес
    факта ровно 2/7 — введено 60 → (2·25 + 5·60) / 7 = 50, без введённого → (2·25 + 5·a) / 7; строка на день прогона
    (store_offsets) — та же смесь."""
    obs = _exact_obs() + _two_visits(201, [25 + 12 * 0.5]) + _two_visits(202, [25 + 12 * 0.5] * 2)
    cur = lambda x: 8 * x.n + 6 * x.tonnes   # noqa: E731
    for manual in ({}, {201: 60.0, 202: 60.0}):
        p = lr.fit_unload(obs, cur, TODAY, manual).params
        a = p['per_stop_min']
        times = lr.store_times(a, manual, p)
        n, fact = p['store_stats']['202']
        prior = manual.get(202, a)
        assert n == 2 and fact == pytest.approx(25, abs=0.1) and times[202][1] == 'learned'
        assert (a + times[202][0] - prior) / (fact - prior) == pytest.approx(2 / 7, abs=0.003)
        assert p['store_offsets']['202'] == times[202][0] and '201' not in p['store_offsets']
        if manual:
            assert a + times[202][0] == pytest.approx(50.0, abs=0.06)
            assert p['store_stats']['201'][0] == 1 and times[201] == (60.0 - a, 'manual')
        else:
            assert '201' not in p['store_stats'] and 201 not in times                  # норма


def test_fit_unload_stores_from_5_visits_same_as_before(monkeypatch):
    """Порог 2 вместо 5 не трогает магазины с ≥ 5 визитами: те же a, b, store_stats и поправки; в строку добавляются
    только магазины с 2–4 визитами. У магазина с введённым временем store_stats были и при пороге 5 (203, 3 визита) —
    теперь и старая строка смешивает его с фактом, ровно как новая."""
    start = TODAY - timedelta(days=lr.HOLDOUT_DAYS + 20)
    obs = _exact_obs({101: 10.0, 102: -1.0})
    for k, cid in enumerate((201, 202, 203, 204), start=1):                           # k визитов, своё время 4 + 3k
        obs += [lr.UnloadObs(d, 1, 0.5, 4 + 3 * k + 12 * 0.5, (cid,)) for d in _days(k, start=start)]
    manual = {101: 30.0, 203: 20.0}
    cur = lambda x: 8 * x.n + 6 * x.tonnes   # noqa: E731
    new = lr.fit_unload(obs, cur, TODAY, manual).params
    with monkeypatch.context() as m:
        m.setattr(lr, 'STORE_MIN_OBS', 5)
        old = lr.fit_unload(obs, cur, TODAY, manual).params
    many = {c for c, (n, _) in new['store_stats'].items() if n >= 5}
    assert many == {str(c) for c in range(100, 106)}
    assert (new['per_stop_min'], new['per_tonne_min']) == (old['per_stop_min'], old['per_tonne_min'])
    assert {c: v for c, v in new['store_stats'].items() if c in many} == \
        {c: v for c, v in old['store_stats'].items() if c in many}
    assert {c: v for c, v in new['store_offsets'].items() if c in many} == \
        {c: v for c, v in old['store_offsets'].items() if c in many}
    assert set(new['store_stats']) - set(old['store_stats']) == {'202', '204'}         # 201 — один визит
    assert old['store_stats']['203'] == new['store_stats']['203'] and '203' not in old['store_offsets']
    a = new['per_stop_min']
    assert lr.store_times(a, manual, old)[203] == lr.store_times(a, manual, new)[203]
    assert lr.store_times(a, manual, new)[203][1] == 'learned'


@pytest.mark.parametrize('prior', [12.0, None])
def test_fit_unload_two_visits_extreme_in_cap_moves_time_by_seventh(prior):
    """Два визита, один — крайний, но в пределах отсечения (≤ 3 × действующей нормы и ≤ 90 мин): медиана двух — их
    середина, смесь даёт факту 2/7, значит крайний визит сдвигает время магазина от опоры не больше чем на 1/7 своего
    отрыва. Чуть дольше отсечения — визит выброшен, остаётся один: время не меняется вовсе."""
    manual = {} if prior is None else {205: prior}
    plain = lambda o: 8 * o.n + 6 * o.tonnes   # noqa: E731
    ext = lr.store_extras(8.0, manual, None)
    cur = lambda o: plain(o) + math.fsum(ext.get(c, 0.0) for c in o.customers)   # noqa: E731
    cap = lr.UNLOAD_CAP_REL * cur(lr.UnloadObs(TODAY, 1, 0.5, 0.0, (205,)))   # действующая норма не меньше общей
    assert cap <= lr.UNLOAD_MAX_MIN
    normal = (prior or 4.0) + 12 * 0.5                     # обычный визит — ровно опора (без введённого — a = 4)
    p = lr.fit_unload(_exact_obs() + _two_visits(205, [normal, cap - 1.0]), cur, TODAY, manual, plain).params
    a, b = p['per_stop_min'], p['per_tonne_min']
    base = prior or a
    extreme = cap - 1.0 - b * 0.5                          # постоянная часть крайнего визита
    n, fact = p['store_stats']['205']
    extra, src = lr.store_times(a, manual, p)[205]
    swing = a + extra - base
    assert (n, src) == (2, 'learned') and extreme - base > 20
    assert swing == pytest.approx(2 / 7 * (fact - base), abs=0.07)
    assert 0 < swing <= (extreme - base) / 7 + 0.07
    out = lr.fit_unload(_exact_obs() + _two_visits(205, [normal, cap + 1.0]), cur, TODAY, manual, plain).params
    assert out['store_stats'].get('205', [1])[0] == 1
    assert a + lr.store_extras(a, manual, out).get(205, 0.0) == pytest.approx(base, abs=0.06)


def test_page_and_hint_follow_second_visit_threshold(client, monkeypatch):
    """Страница обучения и подсказка «Условий магазина» — тот же порог, что расчёт: 1 визит — «пока мало» (введённое
    или норма), 2 — уже по факту: (2·41 + 5·6) / 7 = 16."""
    state = _learning_client(client, monkeypatch)
    state.store.save_customer_constraints(101, None, None, 'qa', 20)
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {'102': 10.0},
        'store_stats': {'101': [1, 41.0], '102': [2, 41.0]}})])
    stores = next(s for s in client.get('/api/routes/learning/status').get_json()['status']
                  if s['kind'] == 'unload')['stores']
    rows = {r['customer_id']: r for r in stores['rows']}
    assert stores['min_visits'] == 2
    assert (rows[101]['visits'], rows[101]['in_calc_min'], rows[101]['source']) == (1, 20.0, 'manual')
    assert (rows[102]['visits'], rows[102]['in_calc_min'], rows[102]['source']) == (2, 16.0, 'learned')
    hint = {c['customer_id']: c for c in client.get('/api/routes/customer-vehicles?q=C10').get_json()['customers']}
    assert (hint[101]['unload_visits'], hint[101]['unload_auto_min']) == (None, 6.0)
    assert (hint[102]['unload_visits'], hint[102]['unload_auto_min']) == (2, 16.0)
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    assert f'id="lrStoresMin">{lr.STORE_MIN_OBS}</span>' in html and 'Со второй разгрузки' in html
    assert 'не меньше 5 раз' not in html

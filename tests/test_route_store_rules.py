# -*- coding: utf-8 -*-
"""Правило магазина «отдельный рейс + своя машина + центр можно» (ответ владельца №78, 16–17: «Ռամադա» — всегда отдельно,
FORD 333NO33, в центр этой машине можно).

- отдельный рейс (store.customer_solo, схема 24): заказ магазина едет рейсом «склад → магазин → склад»; второй рейс машины
  — как обычно; сборка (с окнами и без, с PyVRP), «Везти после конца дня», перенос конца рейса и новые заказы дня его не
  смешивают с другими магазинами;
- допуск «только выбранные» у магазина в малом центре пускает выбранные машины в центр только ради него;
- без правил план — прежний до байта (хэш HEAD в test_route_loading_season), миграция 23 → 24 — только новая таблица;
- карточка «Условия магазина»: флажок «Առանձին երթ» сохраняется вместе с остальным.

Синтетические данные, без ERP.  Запуск:  python -m pytest tests/test_route_store_rules.py -q
"""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import vrp  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_loading_season import CODES, FORD, HOWO, JAC, TN, _ctx, _info, _stops  # noqa: E402

RAMADA = 105                       # магазин сценария: в «центре» ниже, 865 кг — как 06.10
ZONE = ((40.17, 44.42), (40.20, 44.42), (40.20, 44.48), (40.17, 44.48))   # «центр» вокруг части точек сценария


def _ramada_stops():
    return [replace(s, kg=865.0) if s.customer_id == RAMADA else s for s in _stops()]


def _trips_with(draft, cid):
    return [t for t in draft.trips if cid in t.stops]


@pytest.mark.parametrize('tn', [TN, replace(TN, preload=True)])
def test_solo_store_gets_its_own_trip(tn):
    ss = _ramada_stops()
    ctx = _ctx(tn=tn, solo=frozenset({RAMADA}))
    draft = dp.build(ctx, ss, None, CODES, 'now')
    got = _trips_with(draft, RAMADA)
    assert len(got) == 1 and got[0].stops == [RAMADA]
    view = dp.plan_view(ctx, ss, draft, _info)
    assert view['unassigned'] == []
    # без правила тот же магазин едет вместе с другими
    plain = dp.build(_ctx(tn=tn), ss, None, CODES, 'now')
    assert len(_trips_with(plain, RAMADA)[0].stops) > 1
    # пересборка с закреплённым рейсом другой машины — правило то же (раскладка вокруг закреплённых)
    other = next(t for t in draft.trips if RAMADA not in t.stops)
    other.pinned = True
    again = dp.build(ctx, ss, draft, CODES, 'now2')
    assert [t.stops for t in _trips_with(again, RAMADA)] == [[RAMADA]]


def test_solo_with_window_and_overtime_paths():
    ss = _ramada_stops()
    ctx = _ctx(solo=frozenset({RAMADA}))
    ctx = replace(ctx, windows={**ctx.windows, RAMADA: (600.0, 900.0)})
    draft = dp.build(ctx, ss, None, CODES, 'now')
    assert [t.stops for t in _trips_with(draft, RAMADA)] == [[RAMADA]]
    # «Везти после конца дня»: не поместившиеся раскладываются поверх — отдельный магазин и там один
    short = replace(ctx, tn=replace(ctx.tn, work_minutes=300.0), overtime_minutes=660.0)
    d2 = dp.build(short, ss, None, ['333DO33'], 'now')
    d2 = dp.overtime(short, ss, d2)
    assert all(t.stops == [RAMADA] for t in _trips_with(d2, RAMADA))


def test_can_carry_and_vrp_keep_solo_alone():
    ss = {s.customer_id: s for s in _ramada_stops()}
    ctx = _ctx(solo=frozenset({RAMADA}))
    sel = dp._selected(ctx, CODES)
    assert dp._can_carry(ctx, sel, '991AT61', [RAMADA], ss, {})
    assert not dp._can_carry(ctx, sel, '991AT61', [RAMADA, 101], ss, {})
    assert dp._can_carry(_ctx(), sel, '991AT61', [RAMADA, 101], ss, {})
    if not vrp.available():
        pytest.skip('PyVRP не установлен')
    km = [[0.0, 5.0, 5.2], [5.0, 0.0, 0.3], [5.2, 0.3, 0.0]]
    mins = [[0.0, 10.0, 10.5], [10.0, 0.0, 1.0], [10.5, 1.0, 0.0]]
    veh = [vrp.Vehicle('A', 2000.0, 10.0, False)]
    pieces = [vrp.Piece(1, 100.0, 5.0, None, None, False, True), vrp.Piece(2, 100.0, 5.0, None, None, False, True)]
    together = vrp.solve(pieces, km, mins, veh, [vrp.Shift('A', 0.0, 500.0)], [], None)
    assert sorted(len(t) for _, ts in together for t in ts) == [2]
    apart = vrp.solve([pieces[0], replace(pieces[1], solo=True)], km, mins, veh, [vrp.Shift('A', 0.0, 500.0)], [], None)
    assert sorted(len(t) for _, ts in apart for t in ts) == [1, 1]


def test_allow_rule_lets_listed_truck_into_center_for_that_store_only():
    ss = _ramada_stops()
    inside = [s.customer_id for s in ss if s.point is not None and fl.in_polygon(s.point, ZONE)]
    assert RAMADA in inside and len(inside) > 1
    rule = {RAMADA: VehicleAccess('allow', ('333DO33',))}
    ctx = replace(_ctx(solo=frozenset({RAMADA})), center_zone=ZONE, vehicle_access=rule)
    by = {s.customer_id: s for s in ss}
    assert not dp._central(ctx, by[RAMADA]) and dp._central(ctx, by[inside[1] if inside[0] == RAMADA else inside[0]])
    draft = dp.build(ctx, ss, None, CODES, 'now')
    assert [(t.truck, t.stops) for t in _trips_with(draft, RAMADA)] == [('333DO33', [RAMADA])]   # FORD без права въезда
    view = dp.plan_view(ctx, ss, draft, _info)
    for t in view['trucks']:
        for tr in t['trips']:
            for s in tr['stops']:
                assert not s['center_miss'], (t['car_code'], s['customer_id'])
                if s['customer_id'] in inside and s['customer_id'] != RAMADA:
                    assert t['car_code'] == JAC.car_code          # прочий центр — только машина с правом въезда
    # без JAC прочие магазины центра — «центр без машины», а «Ռամադա» центр не держит (здесь FORD занят окном 110 весь
    # день — «не успели», но не «центр» и не «нет машины»)
    d2 = dp.build(ctx, ss, None, ['991AT61', '333DO33'], 'now')
    assert set(inside) - {RAMADA} <= d2.no_center and RAMADA not in d2.no_center | d2.no_vehicle
    # запрет (deny) правило центра не снимает
    deny = replace(ctx, vehicle_access={RAMADA: VehicleAccess('deny', ('991AT61',))})
    assert dp._central(deny, by[RAMADA])


def test_store_schema_24_solo_roundtrip_and_migration(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    assert s.load().solo == frozenset()
    s.save_customer_constraints(26500, VehicleAccess('allow', ('333NO33',)), None, 'qa', solo=True)
    b = s.load()
    assert b.solo == frozenset({26500}) and b.vehicle_access[26500].trucks == ('333NO33',)
    s.save_customer_constraints(26500, VehicleAccess('allow', ('333NO33',)), None, 'qa')   # KEEP — не меняется
    assert s.load().solo == frozenset({26500})
    s.save_customer_constraints(26500, None, None, 'qa', solo=False)
    assert s.load().solo == frozenset()
    with pytest.raises(ValueError):
        s.save_customer_constraints(26500, None, None, 'qa', solo='yes')
    # 23 → 24: только новая таблица, прежние строки как были
    s.save_customer_constraints(101, VehicleAccess('deny', ('X',)), None, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE customer_solo')
        conn.execute("UPDATE meta SET value = '23' WHERE key = 'schema_version'")
        conn.commit()
        before = conn.execute('SELECT * FROM customer_vehicle_access').fetchall()
    b = st.Store(path).load()
    assert b.solo == frozenset() and b.vehicle_access[101].trucks == ('X',)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('24',)
        assert conn.execute('SELECT * FROM customer_vehicle_access').fetchall() == before
    assert 23 in st._MIGRATIONS and st._MIGRATIONS[23] == (st._CUSTOMER_SOLO_TABLE,)


def test_customer_card_api_saves_solo(client):
    from test_route_optimizer import make_snapshot  # noqa: F401
    state = client.application.extensions['route_optimizer']
    cid = next(iter(state.snapshots.get(allow_stale=True)[0].customers))
    body = {'customer_id': cid, 'access': {'mode': 'allow', 'trucks': []}, 'window': None, 'unload_min': None}
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': True}).status_code == 200
    assert state.store.load().solo == frozenset({cid})
    got = client.get(f'/api/routes/customer-vehicles?customer_id={cid}').get_json()['customers'][0]
    assert got['solo'] is True
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': 'x'}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': cid, 'access': None, 'solo': True}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json=body).status_code == 200          # без solo — не меняется
    assert state.store.load().solo == frozenset({cid})
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': False}).status_code == 200
    assert state.store.load().solo == frozenset()


from test_route_optimizer import client  # noqa: E402,F401

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


def test_center_flag_with_allow_rule_lets_listed_truck_in_for_that_store_only():
    ss = _ramada_stops()
    inside = [s.customer_id for s in ss if s.point is not None and fl.in_polygon(s.point, ZONE)]
    assert RAMADA in inside and len(inside) > 1
    rule = {RAMADA: VehicleAccess('allow', ('333DO33',))}
    by = {s.customer_id: s for s in ss}
    # допуск allow без флажка — прежний смысл: правило центра действует; флажок без допуска allow — тоже ничего
    plain = replace(_ctx(), center_zone=ZONE, vehicle_access=rule)
    assert dp._central(plain, by[RAMADA])
    assert dp._central(replace(plain, vehicle_access={}, center_allow=frozenset({RAMADA})), by[RAMADA])
    ctx = replace(_ctx(solo=frozenset({RAMADA})), center_zone=ZONE, vehicle_access=rule, center_allow=frozenset({RAMADA}))
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


def test_store_schema_24_rules_roundtrip_and_migration(tmp_path):
    path = str(tmp_path / 'r.db')
    s = st.Store(path)
    assert (s.load().solo, s.load().center_allow) == (frozenset(), frozenset())
    s.save_customer_constraints(26500, VehicleAccess('allow', ('333NO33',)), None, 'qa', solo=True, center=True)
    b = s.load()
    assert b.solo == b.center_allow == frozenset({26500}) and b.vehicle_access[26500].trucks == ('333NO33',)
    s.save_customer_constraints(26500, VehicleAccess('allow', ('333NO33',)), None, 'qa')   # KEEP — не меняется
    assert s.load().solo == s.load().center_allow == frozenset({26500})
    s.save_customer_constraints(26500, None, None, 'qa', solo=False)                      # центр остаётся
    assert (s.load().solo, s.load().center_allow) == (frozenset(), frozenset({26500}))
    s.save_customer_constraints(26500, None, None, 'qa', center=False)                    # оба сняты — строки нет
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM customer_rule').fetchone() == (0,)
    with pytest.raises(ValueError):
        s.save_customer_constraints(26500, None, None, 'qa', solo='yes')
    with pytest.raises(ValueError):
        s.save_customer_constraints(26500, None, None, 'qa', center=1)
    # 23 → 24: только новая таблица, прежние строки как были
    s.save_customer_constraints(101, VehicleAccess('deny', ('X',)), None, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE customer_rule')
        conn.execute("UPDATE meta SET value = '23' WHERE key = 'schema_version'")
        conn.commit()
        before = conn.execute('SELECT * FROM customer_vehicle_access').fetchall()
    b = st.Store(path).load()
    assert b.solo == frozenset() and b.vehicle_access[101].trucks == ('X',)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() ==             (str(st.SCHEMA_VERSION),)                                          # 23 → 24 → … → текущая
        assert conn.execute('SELECT * FROM customer_vehicle_access').fetchall() == before
    assert 23 in st._MIGRATIONS and st._MIGRATIONS[23] == (st._CUSTOMER_RULE_TABLE,)


def test_customer_card_api_saves_solo(client):
    from test_route_optimizer import make_snapshot  # noqa: F401
    state = client.application.extensions['route_optimizer']
    cid = next(iter(state.snapshots.get(allow_stale=True)[0].customers))
    body = {'customer_id': cid, 'access': {'mode': 'allow', 'trucks': []}, 'window': None, 'unload_min': None}
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': True}).status_code == 200
    assert state.store.load().solo == frozenset({cid})
    got = client.get(f'/api/routes/customer-vehicles?customer_id={cid}').get_json()['customers'][0]
    assert got['solo'] is True and got['center'] is False
    assert client.post('/api/routes/customer-vehicles', json={**body, 'center': True}).status_code == 200
    assert state.store.load().center_allow == frozenset({cid}) and state.store.load().solo == frozenset({cid})
    assert client.post('/api/routes/customer-vehicles', json={**body, 'center': 'x'}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': False, 'center': False}).status_code == 200
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': True}).status_code == 200
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': 'x'}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json={'customer_id': cid, 'access': None, 'solo': True}).status_code == 400
    assert client.post('/api/routes/customer-vehicles', json=body).status_code == 200          # без solo — не меняется
    assert state.store.load().solo == frozenset({cid})
    assert client.post('/api/routes/customer-vehicles', json={**body, 'solo': False}).status_code == 200
    assert state.store.load().solo == frozenset()




def test_pinned_trip_with_solo_store_and_others_keeps_solver():
    """Закреплённый (загруженный) рейс, где отдельный магазин едет с другими (правка логиста), — как есть: проверка
    отдельного рейса его не касается, решатель PyVRP не отключается на весь день (№78, ревью п. 4)."""
    if not vrp.available():
        pytest.skip('PyVRP не установлен')
    import unittest.mock as um
    ss = _ramada_stops()
    ctx = _ctx(solo=frozenset({RAMADA}))
    seen = []
    real = fl._solver_try

    def spy(*a, **k):
        got = real(*a, **k)
        seen.append(got[1] if got[0] is None else 'ok')
        return got
    old = dp.Draft(trips=[dp.DraftTrip(1, '991AT61', [RAMADA, 101, 102], pinned=True)], next_id=2)
    with um.patch.object(fl, '_solver_try', spy):
        draft = dp.build(ctx, ss, old, CODES, 'now')
    assert dp.trip_of(draft, 1).stops == [RAMADA, 101, 102] and 'load' not in seen and 'ok' in seen


def test_solo_truck_does_second_trip_instead_of_extra_truck():
    """Ответ владельца №78, 18: лишняя машина без нужды не берётся (_spare_solo_truck): машин в плане не больше, чем без
    правила, все магазины в рейсах; без нужды — см. test_spare_solo_truck_drops_extra_truck_when_everything_fits."""
    ss = _ramada_stops()
    rule = {RAMADA: VehicleAccess('allow', ('333DO33',))}
    plain = dp.build_crewed(replace(_ctx(), vehicle_access=rule), ss, None, CODES, 'now', dp.Crew())
    ctx = replace(_ctx(solo=frozenset({RAMADA})), vehicle_access=rule)
    draft = dp.build_crewed(ctx, ss, None, CODES, 'now', dp.Crew())
    assert len({t.truck for t in draft.trips}) <= len({t.truck for t in plain.trips}) and draft.trucks == sorted(CODES)
    assert not (draft.no_room | draft.no_window | draft.no_center | draft.no_vehicle)
    assert [t.stops for t in _trips_with(draft, RAMADA)] == [[RAMADA]]
    # здесь без любой из двух других машин магазины не помещаются (окна, тоннаж) — третья машина нужна, план как есть
    for drop in ('991AT61', '475DD61'):
        d2 = dp.build(ctx, ss, None, [c for c in CODES if c != drop], 'now', dp.SEAT_TRIAL_ITERATIONS)
        assert d2.no_room | d2.no_window | d2.no_center | d2.no_vehicle


def test_spare_solo_truck_drops_extra_truck_when_everything_fits():
    """Машина только с отдельным рейсом и лишняя машина: проба без лишней — всё помещается, итог без неё; Draft.trucks
    прежний (машина остаётся отмеченной)."""
    ss = [s for s in _ramada_stops() if s.customer_id in (RAMADA, 101, 102, 103)]
    rule = {RAMADA: VehicleAccess('allow', ('333DO33',))}
    ctx = replace(_ctx(solo=frozenset({RAMADA})), vehicle_access=rule, windows={})
    lone = dp.Draft(trucks=sorted(CODES), trips=[dp.DraftTrip(1, '333DO33', [RAMADA]),
                                                 dp.DraftTrip(2, '991AT61', [101, 102, 103])], next_id=3)
    assert dp._solo_only(ctx, lone) == {'333DO33'}
    got = dp._spare_solo_truck(ctx, ss, dp.Draft(), dp.Draft.from_json(lone.to_json()), 'now')
    got, n = got
    assert [t.truck for t in got.trips] == ['333DO33', '333DO33'] and got.trucks == sorted(CODES) and not got.no_room
    assert [t.stops for t in got.trips if RAMADA in t.stops] == [[RAMADA]]           # второй рейс — обычные магазины
    assert n == 1 and got.solo_spare['truck'] == '991AT61' and got.solo_spare['limit_pct'] == 5.0
    assert dp.Draft.from_json(got.to_json()).solo_spare == got.solo_spare
    assert dp.plan_view(ctx, ss, got, _info)['explain']['solo_spare'] == got.solo_spare
    # ответ 20: ֏ дня без лишней машины растёт больше порога — лишняя машина остаётся, машина отдельного рейса — свой магазин
    dear = replace(ctx, solo_spare_max_pct=0.0, trucks={**ctx.trucks, '333DO33': replace(FORD, l100=80.0)})
    assert got.solo_spare['delta_pct'] < 0                         # здесь без HOWO даже дешевле (FORD экономнее)
    kept, _ = dp._spare_solo_truck(dear, ss, dp.Draft(), dp.Draft.from_json(lone.to_json()), 'now')
    assert [t.truck for t in kept.trips] == ['333DO33', '991AT61'] and kept.solo_spare['truck'] is None
    assert kept.solo_spare['delta_pct'] > 0 and kept.solo_spare['limit_pct'] == 0.0
    # машина отдельного рейса другие магазины не возит (допуск) — без проб
    deny = {**rule, **{c: VehicleAccess('deny', ('333DO33',)) for c in (101, 102, 103)}}
    same, n = dp._spare_solo_truck(replace(ctx, vehicle_access=deny), ss, dp.Draft(), dp.Draft.from_json(lone.to_json()),
                                   'now')
    assert n == 0 and same.solo_spare is None and [t.truck for t in same.trips] == ['333DO33', '991AT61']


def test_live_center_alarm_only_off_the_flagged_store():
    """Карта машин: заезд в центр к магазину с правилом — без тревоги, другой заезд той же машины в центр — тревога."""
    from datetime import datetime, timedelta
    from route_optimizer import live
    from route_optimizer.geo import Fix
    zone = ((40.17, 44.50), (40.19, 44.50), (40.19, 44.52), (40.17, 44.52))
    rules = live.Rules(center_zone=zone)
    t0 = datetime(2026, 10, 6, 10, 0, tzinfo=live.YEREVAN)
    store, other, out = (40.18, 44.51), (40.175, 44.505), (40.16, 44.49)

    def run(p, k0):
        return [Fix(t0 + timedelta(minutes=k0 + i), p[0], p[1], 5.0) for i in range(3)]
    pts = run(store, 0) + run(out, 10) + run(other, 20) + run(out, 30)
    stops = [{'customer_id': 26500, 'lat': store[0], 'lon': store[1]}]
    assert len(live.center_alerts(pts, rules, live.TruckSpec(center_ok=False), False, stops)) == 2
    flagged = live.TruckSpec(center_ok=False, center_customers=frozenset({26500}))
    got = live.center_alerts(pts, rules, flagged, False, stops)
    assert len(got) == 1 and got[0]['lat'] == other[0]


from test_route_optimizer import client  # noqa: E402,F401


def test_spare_solo_truck_removed_truck_keeps_no_seat_and_settings():
    """Снятая машина — без посадки сборки (№77) у неё; настройка порога — 0…100, по умолчанию 5."""
    ss = [s for s in _ramada_stops() if s.customer_id in (RAMADA, 101, 102, 103)]
    rule = {RAMADA: VehicleAccess('allow', ('333DO33',))}
    ctx = replace(_ctx(solo=frozenset({RAMADA})), vehicle_access=rule, windows={})
    crew = dp.Crew({'333DO33': 'Ա', '991AT61': 'Բ', '475DD61': 'Գ'}, frozenset(), frozenset({'Ա'}))
    draft = dp.build_crewed(ctx, ss, None, CODES, 'now', crew, timing := {})
    assert 'solo_trials' in timing and 'solo_seconds' in timing
    if draft.solo_spare and draft.solo_spare['truck']:
        assert draft.solo_spare['truck'] not in draft.seats
    base = dict(st.DEFAULT_SETTINGS)
    assert st.validate_settings(base, None)[0]['solo_spare_max_pct'] == 5
    for bad in (-1, 101, None, '5'):
        assert 'solo_spare_max_pct' in st.validate_settings({**base, 'solo_spare_max_pct': bad}, None)[1], bad

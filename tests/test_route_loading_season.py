# -*- coding: utf-8 -*-
"""«Развоз»: запас в конце дня и погрузка по сезону (ответ владельца №78, docs/plans/loading-season-plan.md, пп. 1–2).

- запас (truck_end_reserve_min): сборка возвращает машины не позже конца дня минус запас; рейс, вернувшийся в запасе
  (закреплён), — не опоздание, мягкая пометка in_reserve; опоздание — как раньше, от конца дня;
- сезон (morning_loading_from … to, ММ-ДД, через Новый год): в сезоне первый рейс грузится утром, вне сезона загружен с
  вечера и выезжает в начале дня; второй рейс и новый рейс с заказами дня (№72) — с загрузкой;
- без нового поведения (запас 0, сезон «всегда утром») план совпадает с HEAD 31e20c9 байт в байт (хэш снят там);
- карта машин (ETA до выезда) и обучение загрузки — по тому же правилу; настройки — проверка ММ-ДД.

Синтетические данные; ERP не читается; базы — временные.  Запуск:  python -m pytest tests/test_route_loading_season.py -q
"""
import hashlib
import json
import sys
import uuid
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import evaluate as ev  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import geo  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import live  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import vrp  # noqa: E402

DEPOT = (40.19462, 44.6004)
NORMS = ev.Norms.from_settings(st.DEFAULT_SETTINGS)
# загрузка 60 мин + 10 мин/т (ответ 8), обед 30 мин с 12:30 до 14:30 — как на CT115
TN = fl.TruckNorms(work_minutes=540.0, unload_min_per_stop=10.0, unload_min_per_tonne=10.0,
                   warehouse_load_fixed_min=60.0, warehouse_load_min_per_tonne=10.0, work_start_minute=540.0,
                   lunch_minutes=30.0, lunch_from=210.0, lunch_to=330.0)
HOWO = fl.FleetTruck('991AT61', 'HOWO', 5000.0, 20.0)
FORD = fl.FleetTruck('333DO33', 'FORD', 2500.0, 15.0)
JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 10.0, center_ok=True)
CODES = ['991AT61', '333DO33', '475DD61']
SPEC = [(101 + i, (40.13 + 0.011 * (i % 7), 44.43 + 0.017 * (i // 7) + 0.004 * (i % 3)), 260.0 + 37 * (i % 5))
        for i in range(24)]
WINDOWS = {103: (600.0, 720.0), 110: (840.0, 1020.0)}
# план и вид сценария _run без нового поведения на HEAD 31e20c9 (до №78): тот же код — тот же хэш
HEAD_HASH = '9ae156c23895ca8a2f2d6bb45f8bf19104663dc0847c2ee7df0a2d212322f553'


def _stops():
    orders = [dp.DispatchOrder(str(uuid.UUID(int=i + 1)).upper(), f'N{i}', date(2026, 9, 30), cid, 1, '', 10000.0, kg,
                               None) for i, (cid, _, kg) in enumerate(SPEC)]
    pts = {cid: p for cid, p, _ in SPEC}
    return dp.build_stops(orders, lambda c: geo.Coord(*pts[c], 'erp'))


def _ctx(tn=TN, **kw):
    return dp.DayContext(date(2026, 10, 1), DEPOT, {t.car_code: t for t in (HOWO, FORD, JAC)}, NORMS, tn, 540,
                         120.0, 0.0, WINDOWS, **kw)


def _info(s):
    return {'customer_id': s.customer_id}


def _run(ctx):
    """Сборка, закрепление первого рейса, пересборка; (хэш планов и видов без новых полей сводки, черновик, вид)."""
    ss = _stops()
    d1 = dp.build(ctx, ss, None, CODES, 'now')
    d1.trips[0].pinned = True
    d2 = dp.build(ctx, ss, d1, CODES, 'now2')
    views = []
    for d in (d1, d2):
        v = dp.plan_view(ctx, ss, d, _info)
        v['summary'].pop('preload', None)
        v['summary'].pop('end_reserve_min', None)
        views.append(v)
    blob = json.dumps([d1.to_json(), d2.to_json(), views], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest(), d2, views[1]


def _clock(text):
    h, m = text.split(':')
    return int(h) * 60 + int(m)


# ============================== без нового поведения — как HEAD ==============================

def test_neutral_settings_plan_is_byte_identical_to_head():
    """Запас 0 и «всегда утром» (preload False): сборка, пересборка с закреплённым рейсом и вид — как до №78."""
    h, _, view = _run(_ctx())
    assert h == HEAD_HASH
    assert all(not tr.get('preloaded') and not tr.get('in_reserve') for t in view['trucks'] for tr in t['trips'])


def test_morning_loading_season_bounds_and_new_year():
    s = {'morning_loading_from': '11-15', 'morning_loading_to': '03-15'}
    for d, want in ((date(2026, 11, 14), False), (date(2026, 11, 15), True), (date(2026, 12, 31), True),
                    (date(2027, 1, 1), True), (date(2027, 3, 15), True), (date(2027, 3, 16), False),
                    (date(2026, 10, 6), False), (date(2028, 2, 29), True)):
        assert dp.morning_loading(d, s) is want, d
    summer = {'morning_loading_from': '06-01', 'morning_loading_to': '08-31'}   # без перехода через год
    assert dp.morning_loading(date(2026, 6, 1), summer) and not dp.morning_loading(date(2026, 9, 1), summer)
    always = {'morning_loading_from': '01-01', 'morning_loading_to': '12-31'}
    assert all(dp.morning_loading(date(2026, 1, 1) + timedelta(days=k), always) for k in range(365))


# ============================== погрузка по сезону ==============================

def test_off_season_first_trip_departs_at_day_start_second_trip_loads():
    ss = _stops()
    _, _, base = _run(_ctx())
    _, draft, view = _run(_ctx(tn=replace(TN, preload=True)))
    assert view['summary']['stops'] == base['summary']['stops'] == len(ss)
    seen = 0
    for t in view['trucks']:
        first, *rest = t['trips']
        assert first['preloaded'] is True and first['loading_minutes'] == 0
        assert first['loading_start'] == first['depart']
        if not any(c['customer_id'] in WINDOWS for c in first['stops']):
            assert first['depart'] == '09:00'                     # выезд в начале дня
        assert first['explain']['loading_min'] == 0 and first['explain']['preloaded'] is True
        for tr in rest:                                           # второй рейс — с загрузкой
            seen += 1
            assert 'preloaded' not in tr and tr['loading_minutes'] >= 60
            assert _clock(tr['depart']) - _clock(tr['loading_start']) == pytest.approx(tr['loading_minutes'], abs=1)
    assert seen >= 1
    # тот же день утром (в сезоне) — у первого рейса загрузка, выезд не раньше 10:00
    for t in base['trucks']:
        assert t['trips'][0]['loading_minutes'] >= 60 and _clock(t['trips'][0]['depart']) >= 600
    # загрузка с вечера — время дня свободнее: км и литры не больше, машин не больше
    assert view['summary']['km'] <= base['summary']['km'] + 0.1
    assert view['summary']['trucks'] <= base['summary']['trucks']
    assert view['summary']['loading_minutes'] < base['summary']['loading_minutes']


def test_off_season_return_time_is_model_time_without_morning_loading():
    """Рейс вне сезона заканчивается ровно на загрузку раньше, чем тот же рейс утром (одна машина, один рейс)."""
    ss = _stops()[:4]
    one = [dp.DraftTrip(1, '991AT61', [s.customer_id for s in ss])]
    a = dp.plan_view(_ctx(), ss, dp.Draft(trucks=['991AT61'], trips=list(one)), _info)['trucks'][0]['trips'][0]
    b = dp.plan_view(_ctx(tn=replace(TN, preload=True)), ss, dp.Draft(trucks=['991AT61'], trips=list(one)),
                     _info)['trucks'][0]['trips'][0]
    assert b['depart'] == '09:00' and _clock(a['depart']) == 540 + round(a['loading_minutes'])
    assert _clock(a['return']) - _clock(b['return']) == pytest.approx(a['loading_minutes'], abs=1)


def test_same_day_trip_with_not_before_loads_even_off_season():
    """Новый рейс с заказами дня (№72, not_before) — загрузка всегда, и первым рейсом машины."""
    ss = _stops()[:3]
    trip = dp.DraftTrip(1, '333DO33', [s.customer_id for s in ss], True, not_before=11 * 60.0)
    view = dp.plan_view(_ctx(tn=replace(TN, preload=True)), ss, dp.Draft(trucks=['333DO33'], trips=[trip]), _info)
    tr = view['trucks'][0]['trips'][0]
    assert 'preloaded' not in tr and tr['loading_start'] == '11:00' and tr['loading_minutes'] >= 60


def test_vrp_shift_before_day_start_is_shifted_not_dropped():
    """PyVRP: промежуток, начавшийся до начала дня (№78), — время сдвинуто; решение то же, что у промежутка с нуля и
    заказом без окна; окно клиента — в тех же минутах дня."""
    if not vrp.available():
        pytest.skip('PyVRP не установлен')
    km = [[0.0, 5.0, 6.0], [5.0, 0.0, 2.0], [6.0, 2.0, 0.0]]
    mins = [[0.0, 10.0, 12.0], [10.0, 0.0, 4.0], [12.0, 4.0, 0.0]]
    pieces = [vrp.Piece(1, 100.0, 5.0, None, None, False, True), vrp.Piece(2, 100.0, 5.0, 30.0, 60.0, False, True)]
    veh = [vrp.Vehicle('A', 1000.0, 10.0, False)]
    early = vrp.solve(pieces, km, mins, veh, [vrp.Shift('A', -60.0, 100.0)], [], None, load_fixed_min=60.0)
    assert early is not None and sorted(i for _, ts in early for t in ts for i in t) == [0, 1]
    # окно 30–60 недостижимо, если промежуток кончается к 30-й минуте дня: сдвиг не «растягивает» окна
    assert vrp.solve(pieces, km, mins, veh, [vrp.Shift('A', -60.0, 25.0)], [], None, load_fixed_min=60.0) is None


# ============================== запас в конце дня ==============================

def test_reserve_build_returns_by_horizon_and_pinned_late_trip_is_soft():
    _, draft, view = _run(_ctx(end_reserve_min=30.0))
    assert view['unassigned'] == []
    for t in view['trucks']:
        assert _clock(t['return']) <= 17 * 60 + 30, t['car_code']   # сборка — не позже 17:30
        assert not t['late'] and not t['over_time'] and 'in_reserve' not in t
    assert dp.plan_view(_ctx(end_reserve_min=30.0), _stops(), draft, _info)['summary']['end_reserve_min'] == 30.0
    # закреплённый вручную рейс, вернувшийся в 17:30–18:00, — «առանց պահուստի», не опоздание
    ss = _stops()
    trip = dp.DraftTrip(1, '991AT61', [s.customer_id for s in ss[:16]], True)
    v = dp.plan_view(_ctx(end_reserve_min=30.0), ss, dp.Draft(trucks=['991AT61'], trips=[trip]), _info)
    t = v['trucks'][0]
    ret = _clock(t['return'])
    if 17 * 60 + 30 < ret <= 18 * 60:
        assert t['in_reserve'] is True and t['trips'][0]['in_reserve'] is True and not t['late']
    else:   # нагрузка рейса не попала в окно запаса — проверка правила напрямую
        assert dp._in_reserve(_ctx(end_reserve_min=30.0), 520.0, 0.0)
        assert not dp._in_reserve(_ctx(end_reserve_min=30.0), 541.0, 0.0)
    r30 = _ctx(end_reserve_min=30.0)
    assert dp._in_reserve(r30, 525.0, 0.0) and not dp._in_reserve(r30, 505.0, 0.0)
    assert not dp._in_reserve(_ctx(), 525.0, 0.0)                 # без запаса пометки нет
    # запас рейса 20 мин (в его минутах) — от запаса дня остаётся 10: возврат 535 — в запасе, 528 — нет (max, не сумма)
    assert dp._in_reserve(r30, 535.0, 20.0) and not dp._in_reserve(r30, 528.0, 20.0)
    assert not dp._in_reserve(r30, 539.0, 45.0)                   # запас рейса больше запаса дня — запаса дня не видно


def test_reserve_and_season_together():
    _, _, view = _run(_ctx(tn=replace(TN, preload=True), end_reserve_min=30.0))
    assert view['unassigned'] == []
    assert all(_clock(t['return']) <= 17 * 60 + 30 for t in view['trucks'])
    assert all(t['trips'][0].get('preloaded') for t in view['trucks'])


# ============================== карта машин, обучение, настройки ==============================

def test_live_eta_before_first_trip_without_loading_off_season():
    from test_route_live import A, DEPOT as LDEPOT, T0, TRUCK, Track, facts, stop
    tr = Track().park(LDEPOT, 5)
    now = tr.t
    stops = [stop('S:A', 1, A, 600.0, seq=1)]
    plan = [live.PlanTrip((1,), {1: T0 + timedelta(minutes=30)}, T0)]
    road = live.Road(1.3, 25.0, 45.0, (40.1792, 44.4991), 12.0, legs=lambda a, b, minute, here: 6.0,
                     unload=lambda p, kg: 10.0)

    rules = live.Rules(load_min=60.0, load_min_per_tonne=10.0)

    def eta(plan):
        card = live.car_view(date(2026, 10, 5), now, facts(tr.pts, stops, [T0, now]), plan, TRUCK, LDEPOT, rules,
                             road, True)
        return next(x for x in card['stops'] if x['stop_id'] == 'S:A')['eta']
    assert eta(plan) == (now + timedelta(minutes=60 + 6 + 6)).isoformat(timespec='seconds')
    assert eta([replace(plan[0], preloaded=True)]) == (now + timedelta(minutes=6)).isoformat(timespec='seconds')
    # отметка — из плана: вне сезона только первый рейс; первый рейс — рейс заказов дня (not_before) — views не помечает
    got = live.plan_trips([[1], [2]], None, date(2026, 10, 5), preloaded=True)
    assert [t.preloaded for t in got] == [True, False]
    assert [t.preloaded for t in live.plan_trips([[1]], None, date(2026, 10, 5))] == [False]


def test_reserve_and_trip_buffer_take_max_not_sum():
    """Запас рейса №66 (b) и запас дня 30 не складываются: последний рейс возвращается не позже 18:00 − max(0, 30 − b)."""
    r = replace(TN, end_reserve=30.0)
    assert (r.end_limit(40.0), r.end_limit(10.0), r.end_limit(0.0)) == (540.0, 520.0, 510.0)
    assert TN.end_limit(40.0) == 540.0 and TN.tail(100.0) == TN.reserve(100.0) == 0.0     # без запаса дня — прежнее
    b = replace(TN, buffer_c=2.6)
    assert replace(b, end_reserve=30.0).tail(100.0) == 30.0 and replace(b, end_reserve=30.0).tail(400.0) == b.reserve(400.0)
    # рейс ≈ 480 мин + запас рейса 2·√480 ≈ 44: по max (18:00 − 0) помещается, по сумме (18:00 − 30) — нет; с окном
    # приёма и без (часовая модель и прежний путь сборки)
    one = replace(TN, unload_min_per_stop=470.0, buffer_c=2.0, warehouse_load_fixed_min=0.0,
                  warehouse_load_min_per_tonne=0.0, lunch_minutes=0.0)
    p = (DEPOT[0] + 0.01, DEPOT[1])
    for windows in (None, [(1.0, 600.0)]):
        def fit(tn):
            return fl.route_day([p], [100.0], [1.0], DEPOT, [HOWO], NORMS, tn, overflow=False, windows=windows)
        assert len(fit(replace(one, end_reserve=30.0))) == 1, windows
        assert fit(replace(one, work_minutes=510.0)) == [], windows
        assert fit(replace(one, end_reserve=90.0)) == [], windows      # запас дня больше запаса рейса — решает он


def test_load_obs_skips_first_trip_off_season():
    Y = ac.YEREVAN
    at = lambda h, m=0: __import__('datetime').datetime(2026, 9, 29, h, m, tzinfo=Y)   # noqa: E731
    stops = [ac.PlanStop('S:1', 101, (40.2, 44.5), 500.0, 500.0), ac.PlanStop('S:2', 102, (40.3, 44.6), 800.0, 800.0)]
    visits = (ac.Visit(('S:1',), at(10), at(10, 12), 0, False), ac.Visit(('S:2',), at(14, 10), at(14, 25), 1, False))
    trips = (ac.Trip(at(9, 30), at(13, 5), 20.0, (0,), 500.0, 10.0), ac.Trip(at(13, 50), at(15), 45.0, (1,), 800.0, 9.0))
    actual = ac.DayActual(100, 19.0, at(9), at(15), (), visits, trips, served=(('S:1', 0), ('S:2', 1)))
    day = date(2026, 9, 29)
    assert [o.tonnes for o in lr.load_obs(day, actual, stops)] == [0.5, 0.8]
    assert [o.tonnes for o in lr.load_obs(day, actual, stops, preloaded=True)] == [0.8]   # первый — загружен с вечера


def test_settings_season_and_reserve_validation():
    base = dict(st.DEFAULT_SETTINGS)
    out, errors = st.validate_settings(base, None)
    assert not errors and (out['morning_loading_from'], out['morning_loading_to'], out['truck_end_reserve_min']) == \
        ('11-15', '03-15', 30)
    for bad in ('02-30', '13-01', '1-15', '15.11', 1115, None):
        _, errors = st.validate_settings({**base, 'morning_loading_from': bad}, None)
        assert 'morning_loading_from' in errors, bad
    _, errors = st.validate_settings({**base, 'morning_loading_to': '02-29'}, None)
    assert 'morning_loading_to' not in errors
    for bad in (-1, 121, None, '30'):
        _, errors = st.validate_settings({**base, 'truck_end_reserve_min': bad}, None)
        assert 'truck_end_reserve_min' in errors, bad


def test_same_day_option_card_first_trip_off_season_without_loading():
    """Карточка варианта новых заказов дня (№72): вставка в первый рейс вне сезона — выезд 09:00 без загрузки (как в плане),
    новый рейс после — с загрузкой."""
    ss = _stops()
    base, extra = ss[:3], ss[3]
    ctx = _ctx(tn=replace(TN, preload=True))
    draft = dp.Draft(trucks=['333DO33'], trips=[dp.DraftTrip(1, '333DO33', [s.customer_id for s in base])], next_id=2)
    # в 06:00 первый рейс ещё «не грузится» (загружен с вечера = загрузка до 09:00); с 08:00 — уже начат, вставки нет
    assert all(o['kind'] != 'insert' for o in dp.same_day_options(ctx, base, ss[:4], draft, [extra.customer_id],
                                                                   -60.0)['options'])
    got = dp.same_day_options(ctx, base, ss[:4], draft, [extra.customer_id], -180.0)['options']
    ins = next(o for o in got if o['kind'] == 'insert')
    assert ins['loading_start'] == ins['depart'] == '09:00'
    trip = next(o for o in got if o['kind'] in ('trip', 'idle'))
    assert _clock(trip['depart']) - _clock(trip['loading_start']) >= 60

# -*- coding: utf-8 -*-
"""«Առաքման արժեք» (№87, п. 6; route_optimizer/cost_to_serve.py): распределение ֏ рейса по магазинам (инварианты денег),
экипаж прямо, пол экономии, накладные ERP (подменённый загрузчик — живой ERP не нужен), период и наценка, API страницы
с кэшем дня, CSV и доступ только администратору. Синтетические данные, временная база маршрутов, без карты дорог."""
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import cost_to_serve as cts  # noqa: E402
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import SQL_COST_SALES, ErpError, check_sql  # noqa: E402
from test_route_optimizer import (DP_DEPOT, _dispatch_setup, _dp_ctx, _dp_stops, _info, _no_road_map,  # noqa: E402,F401
                                  client)

D1, D2 = date(2026, 9, 1), date(2026, 9, 2)
P = cp.Params()                                   # 250 ֏ за точку, 1 750 ֏ за тонну
DEPOT = (0.0, 0.0)


def line_cost(price=100.0, per_kg=0.01):
    """Модель для чистых тестов: км — |Δx| + |Δy| по точкам склад → … → склад, ֏ участка = км × (price + per_kg × остаток
    груза). Машина «NOPE» — без норм (None)."""
    def cost(code, pts, kgs):
        if code == 'NOPE':
            return None
        nodes = [DEPOT, *pts, DEPOT]
        rem, amd, km = sum(kgs), 0.0, 0.0
        for i, (a, b) in enumerate(zip(nodes, nodes[1:])):
            d = abs(a[0] - b[0]) + abs(a[1] - b[1])
            km += d
            amd += d * (price + per_kg * rem)
            if i < len(kgs):
                rem -= kgs[i]
        return cts.Leg(amd, km)
    return cost


def deliv(**kg):
    """{(D1, клиент): Delivery} из c<id>=кг (одна точка экипажа с этими кг — если кг > 0)."""
    return {(D1, int(k[1:])): cts.Delivery(v, int(v > 0), v) for k, v in kg.items()}


# ============================== деньги ==============================

def test_largest_remainder_sums_exactly_and_is_fair():
    assert cts.largest_remainder(10, [1, 1, 1]) == [4, 3, 3]                       # равенство — раньше в списке
    assert cts.largest_remainder(100, [0.5, 0.25, 0.25]) == [50, 25, 25]
    assert cts.largest_remainder(7, [0, 0]) == [4, 3]                               # все веса 0 — поровну
    assert cts.largest_remainder(0, [3, 1]) == [0, 0] and cts.largest_remainder(0, []) == []
    rnd = random.Random(87)
    for _ in range(500):
        n = rnd.randint(1, 12)
        w = [rnd.random() ** 3 * rnd.choice([1, 1e-9, 1e6]) for _ in range(n)]
        total = rnd.randint(0, 10_000_000)
        out = cts.largest_remainder(total, w)
        assert sum(out) == total and all(x >= 0 for x in out)
        s = sum(w)
        assert all(abs(x - total * wi / s) < 1 + 1e-6 for x, wi in zip(out, w))  # каждая доля — в драме от точной
    for bad in ((-1, [1]), (5, [1, -1]), (5, [float('nan')]), (5, [])):
        with pytest.raises(ValueError):
            cts.largest_remainder(*bad)


def test_trip_cost_is_split_exactly_by_removal_saving():
    """Рейс A(1) → B(3) → C(10): без C рейс короче на 14 км — у C самая большая доля; Σ долей = money(C) рейса."""
    coords = {1: (1.0, 0.0), 2: (3.0, 0.0), 3: (10.0, 0.0)}
    cost = line_cost(per_kg=0.0)
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'T', (1, 2, 3))], {}, coords, cost, P)
    full = cost('T', list(coords.values()), [0, 0, 0])
    assert t.fuel == cp.money(full.amd) == 2000 and sum(s.fuel for s in t.stops) == t.fuel
    by = {s.customer_id: s for s in t.stops}
    # экономия: A — 0 (по пути), B — 0, C — 14 км × 100; у A и B — только пол 1 % средней доли
    floor = cts.SAVING_FLOOR * full.amd / 3
    w = [floor, floor, 1400.0]
    assert [by[c].fuel for c in (1, 2, 3)] == cts.largest_remainder(2000, w)
    assert 0 < by[1].fuel <= 10 and 0 < by[2].fuel <= 10 and by[3].fuel > 1950
    assert (by[3].detour_km, by[1].detour_km) == (14.0, 0.0) and t.km == 20.0
    assert all(s.crew == 0 for s in t.stops)                                        # накладных нет — экипажу не за что


def test_zero_and_negative_saving_floor_at_epsilon_not_zero_or_negative():
    """Два магазина в одном доме: удаление любого не сокращает путь (0) — доли малые, но положительные и равные.
    Отрицательная экономия (модель с «скидкой» за точку) тоже — пол, а не минус."""
    coords = {1: (5.0, 0.0), 2: (5.0, 0.0)}
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'T', (1, 2))], {}, coords, line_cost(per_kg=0.0), P)
    assert [s.fuel for s in t.stops] == [500, 500] and t.fuel == 1000

    def odd(code, pts, kgs):        # «без магазина» дороже, чем с ним: экономия < 0
        return cts.Leg(1000.0 + (3 - len(pts)) * 50.0, float(len(pts)))
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'T', (1, 2, 3))], {}, {1: (1, 0), 2: (2, 0), 3: (3, 0)}, odd, P)
    assert t.fuel == 1000 and all(s.fuel > 0 for s in t.stops) and sum(s.fuel for s in t.stops) == 1000


def test_crew_is_direct_per_store_day_and_split_across_trips():
    """Экипаж — ставками «Աշխատավարձ» прямо магазину: 250 + 1 750 × т; магазин в двух рейсах дня (тяжёлый заказ) — одна
    точка, деньги поровну между рейсами (Σ = ровно его сумма), груз в каждом рейсе — половина."""
    coords = {1: (1.0, 0.0), 2: (2.0, 0.0), 3: (4.0, 0.0)}
    delivered = deliv(c1=1000.0, c2=333.0, c3=0.0)
    trips = [cts.Trip(D1, 1, 'T', (1, 2)), cts.Trip(D1, 2, 'T', (1, 3))]
    res = cts.trip_costs(trips, delivered, coords, line_cost(), P)
    crew = {(t.trip_id, s.customer_id): s.crew for t in res for s in t.stops}
    assert crew[(1, 1)] + crew[(2, 1)] == cp.money(250 + 1750 * 1.0) == 2000 and {crew[(1, 1)], crew[(2, 1)]} == {1000}
    assert crew[(1, 2)] == cp.money(250 + 1750 * 0.333) == 833
    assert crew[(2, 3)] == 0                                                         # нет учтённой накладной — не точка
    assert sum(t.crew for t in res) == 2000 + 833
    # груз магазина 1 — по половине в каждом рейсе (как plan_view: кг / k)
    seen = []
    cts.trip_costs(trips, delivered, coords, lambda code, pts, kgs: seen.append((len(pts), list(kgs))) or cts.Leg(1, 1), P)
    assert (2, [500.0, 333.0]) in seen and (2, [500.0, 0.0]) in seen


def test_unpriced_truck_and_store_without_coords():
    coords = {1: (1.0, 0.0), 2: None}
    delivered = deliv(c1=100.0, c2=200.0)
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'NOPE', (1, 2))], delivered, coords, line_cost(), P)
    assert not t.priced and t.fuel == 0 and t.crew == cp.money(250 + 175) + cp.money(250 + 350)
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'T', (1, 2, 1))], delivered, coords, line_cost(), P)   # повтор схлопнут
    by = {s.customer_id: s for s in t.stops}
    assert t.priced and len(t.stops) == 2 and not by[2].routed and by[2].fuel == 0 and by[2].crew == 600
    assert by[1].fuel == t.fuel > 0                                                   # весь рейс — на магазин в пути
    [t] = cts.trip_costs([cts.Trip(D1, 1, 'T', (2,))], delivered, coords, line_cost(), P)      # в пути никого
    assert t.priced and t.fuel == 0 and t.km == 0 and t.stops[0].crew == 600


def test_random_trips_never_lose_or_add_a_dram():
    rnd = random.Random(6)
    coords = {c: (rnd.uniform(-30, 30), rnd.uniform(-30, 30)) for c in range(1, 40)}
    coords[39] = None
    for day in (D1, D2):
        trips, delivered = [], {}
        for tid in range(1, 9):
            stops = tuple(rnd.sample(range(1, 40), rnd.randint(1, 9)))
            trips.append(cts.Trip(day, tid, rnd.choice(['T', 'T', 'NOPE']), stops))
        for c in range(1, 40):
            if rnd.random() < 0.8:
                kg = rnd.uniform(0, 900)
                delivered[(day, c)] = cts.Delivery(kg, rnd.choice([0, 1, 1, 2]), kg * rnd.random())
        res = cts.trip_costs(trips, delivered, coords, line_cost(), P)
        for t in res:
            assert sum(s.fuel for s in t.stops) == t.fuel and sum(s.crew for s in t.stops) == t.crew
            assert all(s.fuel >= 0 and s.crew >= 0 for s in t.stops)
        # экипаж магазина за день — ровно один раз, сколько бы рейсов его ни везли
        for c in {c for t in trips for c in t.stops}:
            d = delivered.get((day, c))
            want = cp.money(P.rate_point * d.points + P.rate_tonne * d.crew_kg / 1000) if d and d.points else 0
            assert sum(s.crew for t in res for s in t.stops if s.customer_id == c) == want
        rep = cts.report(res, {}, None)
        assert rep.fuel + rep.crew == sum(t.fuel + t.crew for t in res) == sum(r.cost for r in rep.rows)


def test_same_model_as_plan_view():
    """Σ долей рейса = «֏ рейса» страницы «Развоз» (plan_view: operating_cost_amd по fl.trip_running_cost)."""
    lat, lon = DP_DEPOT
    spec = [(101, (lat + 0.01, lon - 0.04), 400.0), (102, (lat + 0.02, lon - 0.05), 300.0),
            (103, (lat - 0.01, lon + 0.03), 1200.0)]
    stops, _ = _dp_stops(spec)
    ctx = _dp_ctx()
    draft = dp.Draft(trucks=['991AT61'], trips=[dp.DraftTrip(1, '991AT61', [101, 102, 103])])
    view = dp.plan_view(ctx, stops, draft, _info, explain=False)
    shown = view['trucks'][0]['trips'][0]['operating_cost_amd']

    def leg(code, pts, kgs):
        c = fl.trip_running_cost(pts, kgs, ctx.depot, ctx.norms, ctx.trucks[code])
        return cts.Leg(c.total_amd(ctx.tn.fuel_price), 0.0)
    delivered = {(D1, c): cts.Delivery(kg, 1, kg) for c, _, kg in spec}
    [t] = cts.trip_costs([cts.Trip(D1, 1, '991AT61', (101, 102, 103))], delivered, {c: p for c, p, _ in spec}, leg, P)
    assert abs(t.fuel - shown) <= 1 and sum(s.fuel for s in t.stops) == t.fuel


@pytest.mark.parametrize('hilly', [False, True])
def test_view_leg_equals_trip_running_cost(hilly):
    """views._cost_leg (участки и подъёмы из памяти — ради скорости) считает ровно как fl.trip_running_cost (plan_view):
    ֏ и км любого подмножества точек рейса, с рельефом и без."""
    pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    from dataclasses import replace
    from route_optimizer import roads as rd
    from test_route_center_bypass import GRAPH, _at
    from test_route_optimizer import DP_DAY, DP_NORMS, TN
    from test_route_terrain import LOADED, _hilly
    roads = _hilly() if hilly else rd.RoadDistances.for_graph(GRAPH)
    ctx = dp.DayContext(DP_DAY, _at(6, 3), {LOADED.car_code: LOADED}, replace(DP_NORMS, roads=roads), TN, 9 * 60)
    pts = [_at(0, 0), _at(3, 0), _at(5, 5), _at(2, 4)]
    leg, terrain, pending = views._cost_leg(ctx, pts)
    assert terrain is hilly and pending is False
    rnd = random.Random(3)
    for _ in range(20):
        sub = rnd.sample(pts, rnd.randint(0, 4))
        kgs = [rnd.uniform(0, 800) for _ in sub]
        want = fl.trip_running_cost(sub, kgs, ctx.depot, ctx.norms, LOADED)
        got = leg(LOADED.car_code, sub, kgs)
        assert got.amd == pytest.approx(want.total_amd(ctx.tn.fuel_price), rel=1e-12, abs=1e-9)
        nodes = [ctx.depot, *sub, ctx.depot]
        assert got.km == pytest.approx(sum(ctx.norms.for_trucks().km(a, b) for a, b in zip(nodes, nodes[1:])))
        assert (want.terrain_liters is not None) == hilly                    # рельеф действительно участвовал
    assert leg('NOPE', pts, [0.0] * 4) is None


# ============================== ERP и отчёт ==============================

# 1 — менеджер, 2 — линия 19 л, 11/12 — экспедиторы, 13 и 14 — один код B008/10 дважды (помощник-исключение по умолчанию)
AGENTS = {1: 'A001/4', 2: 'A008/3', 11: 'B001/1', 12: 'B002/1', 13: 'B008/10', 14: ' b008/10 '}


def sale(day, c, total, kg=0.0, crew=True, line=1, van=11):
    return cts.Sale(day, c, line, van if crew else line, total, kg)


def test_deliveries_mirror_crew_pay_points():
    data = cts.SalesData((sale(D1, 1, 1000, 100), sale(D1, 1, 500, 50, line=2),           # 19 л — не груз экипажа
                          sale(D1, 2, 9000, 300, crew=False),                             # вёз сам менеджер
                          sale(D1, 3, 0.2, 0.0001), sale(D1, 4, -500, -10), sale(D2, 1, 10, 0)), AGENTS, {})
    got = cts.deliveries(data, P.excluded_lines, P.excluded_people)
    assert got[(D1, 1)] == cts.Delivery(100.0, 1, 100.0)
    assert (D1, 2) not in got
    assert got[(D1, 3)].points == 0 and got[(D1, 4)] == cts.Delivery(0.0, 0, 0.0)       # шум float и возврат — не точка
    assert got[(D2, 1)].points == 1                                                       # сумма есть, веса нет — точка
    assert cts.deliveries(data, ())[(D1, 1)].kg == 150.0
    sales = cts.sales_by_customer(data)
    assert sales[1] == 1510 and sales[2] == 9000                                           # продажи — все накладные
    assert cts.sales_by_customer(data, {D2}) == {1: 10}                                   # только дни с планом


def test_crew_points_per_expeditor_like_crew_pay():
    """Как crew_pay.compute: точка — (экспедитор, день, магазин); двое вёзли одному магазину — две точки; экспедитор из
    excluded_people (все fID его кода, без учёта регистра и пробелов) — ни точки, ни тонн зарплаты, но груз машины — да."""
    data = cts.SalesData((sale(D1, 1, 1000, 100, van=11), sale(D1, 1, 2000, 200, van=12),
                          sale(D1, 2, 3000, 300, van=13), sale(D1, 2, 100, 10, van=14), sale(D1, 2, 500, 40, van=11),
                          sale(D1, 3, 700, 70, van=13)), AGENTS, {})
    got = cts.deliveries(data, P.excluded_lines, P.excluded_people)
    assert got[(D1, 1)] == cts.Delivery(300.0, 2, 300.0)
    assert got[(D1, 2)] == cts.Delivery(350.0, 1, 40.0)                                  # 13 и 14 — один код-исключение
    assert got[(D1, 3)] == cts.Delivery(70.0, 0, 0.0)                                    # только помощник: груз без оплаты
    assert got[(D1, 1)].crew_amd(P) == cp.money(250 * 2 + 1750 * 0.3)
    assert got[(D1, 3)].crew_amd(P) == 0 and cts.Delivery(0.0, 1, -500.0).crew_amd(P) == 0   # минус — не доплата
    # то же, что зарплата: Σ сдельной по магазинам = сдельная crew_pay по людям (кроме округления)
    inv = [cp.Invoice(s.van_id, s.day, s.customer_id, s.line_id, s.total, s.kg) for s in data.sales]
    res = cp.compute(cp.CrewData(tuple(inv), {k: (v.strip(), f'n{k}') for k, v in AGENTS.items()}), P)
    assert sum(r.piece for r in res.rows) == sum(d.crew_amd(P) for d in got.values())


def test_report_visits_flags_and_totals():
    trips = [cts.TripCost(D1, 1, 'T', 10, 1000, 500, True, (cts.StopShare(1, 600, 250, 2.0, True),
                                                             cts.StopShare(2, 400, 250, 1.0, True))),
             cts.TripCost(D1, 2, 'T', 5, 300, 0, True, (cts.StopShare(1, 300, 0, 5.0, True),)),
             cts.TripCost(D2, 3, 'T', 5, 200, 250, True, (cts.StopShare(3, 200, 250, 5.0, True),))]
    sales = {1: 100_000.0, 2: 10_000.0, 3: 0.0}
    rep = cts.report(trips, sales, 5.0)
    rows = {r.customer_id: r for r in rep.rows}
    assert rows[1].visits == 1 and rows[1].cost == 1150 and rows[1].pct == pytest.approx(1.15) and not rows[1].red
    assert rows[2].pct == pytest.approx(6.5) and rows[2].red                                 # доставка съела больше 5 %
    assert rows[3].pct is None and rows[3].red                                               # продаж нет, доставка есть
    assert [r.customer_id for r in rep.rows] == [1, 2, 3] and rep.red == 2
    assert (rep.fuel, rep.crew, rep.trips) == (1500, 750, 3) and rep.sales == 110_000
    assert rep.pct == pytest.approx(2250 / 110_000 * 100)
    assert cts.report(trips, sales, None).red == 0                                           # без наценки — без красного
    assert [(v.day, v.trip_id) for v in rows[1].trips] == [(D1, 1), (D1, 2)]


def test_check_margin_and_period():
    assert cts.check_margin(None) == (None, None) and cts.check_margin('  ') == (None, None)
    assert cts.check_margin(12.5) == (12.5, None) and cts.check_margin('7,5') == (7.5, None) and cts.check_margin(0)[0] == 0
    for bad in (True, 'abc', -1, 100.01, float('inf'), float('nan'), 10 ** 400, [], {}):
        assert cts.check_margin(bad)[0] is None and cts.check_margin(bad)[1], bad
    today = date(2026, 10, 7)
    assert cts.period(None, None, None, today) == ((date(2026, 9, 7), date(2026, 10, 6)), None)
    assert cts.period('90', None, None, today)[0] == (date(2026, 7, 9), date(2026, 10, 6))
    assert cts.period(None, '2026-07-01', '2026-09-30', today)[0] == (date(2026, 7, 1), date(2026, 9, 30))   # 92 дня
    for args in (('7', None, None), ('x', None, None), (None, '2026-07-01', '2026-10-01'), (None, '2026-10-01', None),
                 (None, '2026-10-02', '2026-10-01'), (None, '2026-10-01', '2026-10-08'), (None, '01.10.2026', '2026-10-02')):
        span, err = cts.period(*args, today)
        assert span is None and err, args
    for args in (('²', None, None), ('٣٠', None, None), ('30.0', None, None), (None, '２０２６-10-01', '2026-10-02')):
        span, err = cts.period(*args, today)
        assert span is None and err, args


def test_plan_source_drafts_only_before_first_sent_day():
    first, today = date(2026, 10, 6), date(2026, 10, 7)
    assert cts.plan_source(date(2026, 10, 5), False, first, today) == 'draft'
    assert cts.plan_source(date(2026, 10, 6), False, first, today) is None              # с первой отправки — только sent
    assert cts.plan_source(date(2026, 10, 6), True, first, today) == 'sent'
    assert cts.plan_source(today, False, None, today) is None                            # сегодня — только отправленный
    assert cts.plan_source(today, True, None, today) == 'sent'
    assert cts.plan_source(date(2026, 9, 1), False, None, today) == 'draft'


def test_sql_is_read_only():
    check_sql(SQL_COST_SALES)
    assert SQL_COST_SALES.count('WITH (NOLOCK)') == 3 and 'GROUP BY' in SQL_COST_SALES


# ============================== API ==============================

NOW = datetime(2026, 10, 7, 10, 0)
YEREVAN = timezone(timedelta(hours=4))
DAY_SENT, DAY_DRAFT, DAY_UNSENT = '2026-10-01', '2026-09-30', '2026-10-02'
NAMES = {101: ('C101', 'Խանութ 101'), 102: ('C102', 'Խանութ 102'), 104: ('C104', 'Խանութ 104'), 999: ('C999', 'Առանց կետի')}


def plan(trips, sent_trips=None):
    """Черновик дня; sent_trips — отправленный водителям снимок (№81) с другими рейсами."""
    d = dp.Draft(trucks=['CAR1', 'CAR2'], trips=[dp.DraftTrip(i, car, list(st)) for i, (car, st) in enumerate(trips, 1)])
    raw = d.to_json()
    if sent_trips is not None:
        snap = dp.Draft(trucks=['CAR1', 'CAR2'],
                        trips=[dp.DraftTrip(i, car, list(st)) for i, (car, st) in enumerate(sent_trips, 1)]).sent_plan()
        raw['released'] = {'at': '2026-10-01T08:00:00', 'by': 'qa'}
        raw['sent'] = {'at': '2026-10-01T08:00:00', 'by': 'qa', 'plan': snap}
    return raw


@pytest.fixture
def cost(client, monkeypatch):
    """Клиент «Маршрутов» с депо и машинами CAR1/CAR2 (_dispatch_setup), «сегодня» — 07.10.2026 по Еревану, роль — admin;
    дни планов: 30.09 — черновик до первой отправки (считается), 01.10 — отправленный (в черновике после отправки другие
    рейсы), 02.10 — неотправленный черновик после первой отправки (не считается)."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    monkeypatch.setattr(views, '_yerevan_now', lambda: NOW.replace(tzinfo=YEREVAN))
    app = client.application
    role = {'value': 'admin'}

    @app.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    _dispatch_setup(client, [])
    state = app.extensions['route_optimizer']
    state.store.save_dispatch(DAY_SENT, plan([('CAR1', [104])], sent_trips=[('CAR1', [101, 102]), ('CAR2', [104, 999])]), 'qa')
    state.store.save_dispatch(DAY_DRAFT, plan([('CAR2', [101, 104])]), 'qa')
    state.store.save_dispatch(DAY_UNSENT, plan([('CAR1', [102, 104])]), 'qa')
    calls = []
    d1, d2 = date(2026, 10, 1), date(2026, 9, 30)
    source = {'sales': [sale(d1, 101, 200_000, 400), sale(d1, 102, 20_000, 50), sale(d1, 104, 900_000, 1500),
                        sale(d1, 999, 5_000, 10), sale(d2, 101, 100_000, 300), sale(d2, 104, 300_000, 800),
                        sale(d2, 104, 50_000, 0, crew=False), sale(date(2026, 9, 20), 102, 1_000_000)],
              'error': None}

    def loader(since, until):
        calls.append((since, until))
        if source['error']:
            raise source['error']
        return cts.SalesData(tuple(s for s in source['sales'] if since <= s.day < until), AGENTS, NAMES)
    state.cost_sales_loader = loader
    client.role, client.calls, client.source, client.state = role, calls, source, state
    return client


def test_api_report_uses_sent_plan_then_draft(cost):
    body = cost.get('/api/routes/cost?days=30').get_json()
    assert body['success'] and (body['from'], body['to'], body['days']) == ('2026-09-07', '2026-10-06', 30)
    assert cost.calls == [(date(2026, 9, 7), date(2026, 10, 7))]                  # одним запросом, по вчера включительно
    assert body['sources'] == {'sent': 1, 'draft': 1, 'unsent': 1, 'broken': 0}
    rows = {r['customer_id']: r for r in body['rows']}
    assert set(rows) == {101, 102, 104, 999}
    t = body['totals']
    assert t['trips'] == 3 and t['cost'] == t['fuel'] + t['crew'] == sum(r['cost'] for r in body['rows'])
    assert t['fuel'] == sum(r['fuel'] for r in body['rows']) and t['fuel'] > 0
    # 01.10 — отправленный план: 101 в рейсе CAR1, а не черновик (там только 104)
    assert [(v['date'], v['truck']) for v in rows[101]['trips']] == [('2026-09-30', 'CAR2'), ('2026-10-01', 'CAR1')]
    assert [v['date'] for v in rows[102]['trips']] == ['2026-10-01']                     # 02.10 не отправлен — не считан
    assert rows[101]['visits'] == 2 and rows[102]['visits'] == 1
    # экипаж: 101 — 250 + 1 750 × 0,4 и 250 + 1 750 × 0,3; продажи — накладные дней с планом (20.09 плана нет — его
    # доставка неизвестна, продажи того дня % не занижают)
    assert rows[101]['crew'] == cp.money(250 + 700) + cp.money(250 + 525) and rows[101]['sales'] == 300_000
    assert rows[102]['sales'] == 20_000
    assert rows[999]['unrouted'] and rows[999]['fuel'] == 0 and rows[999]['crew'] == cp.money(250 + 17.5)
    assert rows[104]['sales'] == 1_250_000                                           # и накладная, которую вёз менеджер
    assert body['margin'] is None and t['red'] == 0 and not any(r['red'] for r in body['rows'])
    assert body['rates'] == {'rate_point': 250, 'rate_tonne': 1750} and body['fuel_price'] > 0
    trip = rows[104]['trips'][0]
    assert trip['trip_fuel'] >= trip['fuel'] and trip['trip_stops'] == 2
    assert t['pct'] == pytest.approx(t['cost'] / t['sales'] * 100, abs=0.01)


def test_day_cache_by_plan_revision(cost, monkeypatch):
    monkeypatch.setattr(views, 'COST_FRESH_S', 0.0)      # каждый запрос — новый расчёт периода: проверяем кэш ДНЯ
    seen = []
    real = cts.trip_costs
    monkeypatch.setattr(views.cts, 'trip_costs', lambda trips, *a: seen.append(trips[0].day) or real(trips, *a))
    first = cost.get('/api/routes/cost?days=30').get_json()
    assert sorted(seen) == [date(2026, 9, 30), date(2026, 10, 1)]
    seen.clear()
    assert cost.get('/api/routes/cost?days=30').get_json() == first and seen == []     # оба дня — из кэша
    assert len(cost.calls) == 1                                                          # и накладные — тоже
    cost.state.store.save_dispatch(DAY_DRAFT, plan([('CAR2', [101])]), 'qa')            # новая правка дня 30.09
    body = cost.get('/api/routes/cost?days=30').get_json()
    assert seen == [date(2026, 9, 30)]                                                   # пересчитан только он
    assert [v['date'] for r in body['rows'] if r['customer_id'] == 104 for v in r['trips']] == ['2026-10-01']
    seen.clear()
    with cost.state.cost_lock:                                                           # запись старше часа — пересчёт
        key, costs, _, pending = cost.state.cost_days[date(2026, 10, 1)]
        cost.state.cost_days[date(2026, 10, 1)] = (key, costs, views.time.monotonic() - views.COST_DAY_TTL_SECONDS - 1, pending)
    assert cost.get('/api/routes/cost?days=30').get_json() == body and seen == [date(2026, 10, 1)]
    seen.clear()
    r = cost.post('/api/routes/settings', json={'settings': {'center_zone': [[40.17, 44.49], [40.17, 44.53], [40.20, 44.53]]}})
    assert r.status_code == 200, r.get_json()
    cost.get('/api/routes/cost?days=30')
    assert sorted(seen) == [date(2026, 9, 30), date(2026, 10, 1)]                        # дороги другие — всё заново


def test_margin_flags_red_and_validation(cost):
    assert cost.get('/api/routes/cost/margin').get_json()['value'] is None
    for bad in (150, -1, 'abc', True):
        r = cost.post('/api/routes/cost/margin', json={'value': bad})
        assert r.status_code == 400 and r.get_json()['errors']['value']
    assert cost.post('/api/routes/cost/margin', json=[1]).status_code == 400
    r = cost.post('/api/routes/cost/margin', json={'value': 0.5})
    assert r.status_code == 200 and r.get_json()['value'] == 0.5
    body = cost.get('/api/routes/cost?days=30').get_json()
    red = {r['customer_id'] for r in body['rows'] if r['red']}
    assert body['margin'] == 0.5 and red == {r['customer_id'] for r in body['rows'] if r['pct'] > 0.5}
    assert body['totals']['red'] == len(red) > 0
    assert cost.post('/api/routes/cost/margin', json={'value': None}).get_json()['value'] is None
    assert cost.get('/api/routes/cost?days=30').get_json()['totals']['red'] == 0


def test_margin_store_roundtrip_and_corrupt_row(cost):
    store = cost.state.store
    store.save_cost_margin(12.0, 'boss')
    value, at, by = store.cost_margin()
    assert value == 12.0 and by == 'boss' and at
    assert 'cts_margin_pct' not in store.load().settings                               # не в настройках маршрутов
    store._transaction(lambda conn: conn.execute("UPDATE settings SET value = '{\"value\": 500}' WHERE key = 'cts_margin_pct'"),
                       'qa')
    body = cost.get('/api/routes/cost/margin').get_json()
    assert body['store_error'] and body['value'] is None
    report = cost.get('/api/routes/cost?days=30').get_json()
    assert report['success'] and report['margin_store_error'] and report['totals']['red'] == 0
    assert cost.post('/api/routes/cost/margin', json={'value': 3}).get_json()['store_error'] is False


def test_period_errors_and_custom_range(cost):
    for q in ('days=7', 'from=2026-07-01&to=2026-10-02', 'from=2026-10-05&to=2026-10-01', 'from=2026-10-01&to=2026-10-08'):
        r = cost.get('/api/routes/cost?' + q)
        assert r.status_code == 400 and r.get_json()['errors']['period'], q
    assert cost.calls == []
    body = cost.get('/api/routes/cost?from=2026-10-02&to=2026-10-07').get_json()
    assert body['sources'] == {'sent': 0, 'draft': 0, 'unsent': 1, 'broken': 0}
    assert cost.calls == [(date(2026, 10, 2), date(2026, 10, 8))] and body['rows'] == []
    for q in ('days=²', 'days=٣٠', 'from=２０２６-10-01&to=2026-10-02', 'from=2026-10-01'):
        assert cost.get('/api/routes/cost?' + q).status_code == 400, q
        assert cost.get('/api/routes/cost.csv?' + q).status_code == 400, q


def test_broken_plan_day_is_counted_not_fatal(cost):
    cost.state.store._transaction(lambda conn: conn.execute(
        "INSERT INTO dispatch_plan(day, data, rev, updated_at, updated_by) VALUES('2026-10-03', '{bad', 1, 'x', 'qa')"), 'qa')
    body = cost.get('/api/routes/cost?days=30').get_json()
    assert body['success'] and body['sources']['broken'] == 1 and body['sources']['sent'] == 1


def test_cold_period_is_computed_in_background_and_polled(cost, monkeypatch):
    """Холодный расчёт (дороги без кэша — минуты) не держит запрос: ответ 503 {pending: true}, страница спрашивает снова;
    пока считается один период, другой ждёт очереди; готово — обычный ответ того же расчёта."""
    import threading
    import time
    gate = threading.Event()
    real = cost.state.cost_sales_loader

    def slow(since, until):
        gate.wait(10)
        return real(since, until)
    cost.state.cost_sales_loader = slow
    monkeypatch.setattr(views, 'COST_WAIT_S', 0.05)
    r = cost.get('/api/routes/cost?days=30')
    assert r.status_code == 503 and r.get_json()['pending'] is True and r.get_json()['error'] == views.COST_PENDING
    assert cost.get('/api/routes/cost?days=90').get_json()['pending'] is True       # второй период — в очереди
    assert cost.get('/api/routes/cost.csv?days=30').status_code == 503
    gate.set()
    for _ in range(200):
        r = cost.get('/api/routes/cost?days=30')
        if r.status_code == 200:
            break
        time.sleep(0.05)
    assert r.status_code == 200 and r.get_json()['success'] and r.get_json()['totals']['trips'] == 3
    assert not cost.state.cost_warm


def _count_computes(monkeypatch):
    runs = []
    real = views._cost_compute
    monkeypatch.setattr(views, '_cost_compute', lambda state, since, until: runs.append(since) or real(state, since, until))
    return runs


def test_fresh_result_is_served_without_new_computation(cost, monkeypatch):
    """Готовый итог моложе COST_FRESH_S — без нового расчёта (CSV сразу за страницей); старше — новый расчёт."""
    runs = _count_computes(monkeypatch)
    body = cost.get('/api/routes/cost?days=30').get_json()
    assert cost.get('/api/routes/cost?days=30').get_json() == body
    assert cost.get('/api/routes/cost.csv?days=30').status_code == 200 and len(runs) == 1
    cost.get('/api/routes/cost?days=90')
    assert len(runs) == 2                                                               # другой период — свой расчёт
    with cost.state.cost_lock:
        key = (date(2026, 9, 7), date(2026, 10, 6))
        started, done, res = cost.state.cost_results[key]
        cost.state.cost_results[key] = (started, done - views.COST_FRESH_S - 1, res)
    cost.get('/api/routes/cost?days=30')
    assert len(runs) == 3


def test_saved_margin_never_serves_stale_result(cost, monkeypatch):
    """Наценку сохранили, пока считался период: итог того расчёта не отдаётся (начат раньше) — следующий опрос считает
    заново и красные — по новой наценке. Готовый итог после сохранения тоже не отдаётся."""
    import threading
    import time
    runs = _count_computes(monkeypatch)
    assert cost.get('/api/routes/cost?days=30').get_json()['margin'] is None
    cost.post('/api/routes/cost/margin', json={'value': 0.5})
    body = cost.get('/api/routes/cost?days=30').get_json()                            # моложе 30 с, но до сохранения
    assert body['margin'] == 0.5 and body['totals']['red'] > 0 and len(runs) == 2
    gate = threading.Event()
    real = cost.state.cost_sales_loader
    cost.state.cost_sales_loader = lambda since, until: gate.wait(10) and real(since, until)
    monkeypatch.setattr(views, 'COST_WAIT_S', 0.05)
    cost.state.cost_sales_cache.clear()
    cost.post('/api/routes/cost/margin', json={'value': None})
    assert cost.get('/api/routes/cost?days=30').get_json()['pending'] is True         # считается (наценка None)
    cost.post('/api/routes/cost/margin', json={'value': 100})                         # сохранили во время расчёта
    gate.set()
    for _ in range(200):
        r = cost.get('/api/routes/cost?days=30')
        if r.status_code == 200:
            break
        time.sleep(0.05)
    assert r.get_json()['margin'] == 100 and len(runs) == 4                          # тот расчёт выброшен, новый — с 100


def test_erp_down_is_an_error_not_zeros(cost):
    cost.source['error'] = ErpError('нет связи')
    r = cost.get('/api/routes/cost?days=30')
    assert r.status_code == 503 and r.get_json()['success'] is False and not r.get_json().get('pending')
    [(_, _, err)] = cost.state.cost_results.values()
    assert isinstance(err, ErpError) and err.__traceback__ is None                     # кадры потока не держим
    assert cost.get('/api/routes/cost.csv?days=30').status_code == 503


def test_no_depot_explains_instead_of_500(client, monkeypatch):
    monkeypatch.setattr(views, '_yerevan_now', lambda: NOW.replace(tzinfo=YEREVAN))
    app = client.application

    @app.before_request
    def _role():
        from flask import g
        g.user_role = 'admin'
    state = app.extensions['route_optimizer']
    seen = []
    state.cost_sales_loader = lambda since, until: seen.append(since) or cts.SalesData((), {}, {})
    r = client.get('/api/routes/cost?days=30')
    assert r.status_code == 400 and r.get_json()['error'] == views.COST_NO_CTX and seen == []   # ERP не читали


def test_csv(cost):
    cost.post('/api/routes/cost/margin', json={'value': 0.5})
    r = cost.get('/api/routes/cost.csv?days=30')
    assert r.status_code == 200 and r.mimetype == 'text/csv'
    assert 'cost-to-serve-20260907-20261006.csv' in r.headers['Content-Disposition']
    text = r.get_data(as_text=True)
    assert text.startswith('﻿Առաքման արժեք;2026-09-07;2026-10-06\r\n')
    assert 'Միջին հավելագին, %;0,50' in text and 'Մեկ տոննայի համար, ֏;1750' in text
    body = cost.get('/api/routes/cost?days=30').get_json()
    lines = text.split('\r\n')
    head = lines.index('Կոդ;Խանութ;Այցեր;Դիզել և մաշվածք, ֏;Անձնակազմ, ֏;Առաքում, ֏;Մեկ այցը, ֏;Վաճառք, ֏;Վաճառքից, %;Կարմիր')
    first = lines[head + 1].split(';')
    top = body['rows'][0]
    assert first[0] == top['code'] and int(first[5]) == top['cost'] and first[9] == ('այո' if top['red'] else '')


def test_csv_cell_escapes_formula_names(cost):
    NAMES[102] = ('=C102', '+Խանութ')
    try:
        text = cost.get('/api/routes/cost.csv?days=30').get_data(as_text=True)
    finally:
        NAMES[102] = ('C102', 'Խանութ 102')
    assert "'=C102;'+Խանութ;" in text


@pytest.mark.parametrize('role', [None, 'garage', 'warehouse', 'user'])
def test_only_admin(cost, role):
    cost.role['value'] = role
    for path in ('/routes/cost', '/api/routes/cost', '/api/routes/cost.csv', '/api/routes/cost/margin'):
        assert cost.get(path).status_code == 403, path
    assert cost.post('/api/routes/cost/margin', json={'value': 5}).status_code == 403
    assert cost.calls == [] and cost.state.store.cost_margin()[0] is None
    cost.role['value'] = 'admin'
    assert cost.get('/api/routes/cost').status_code == 200

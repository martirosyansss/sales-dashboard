# -*- coding: utf-8 -*-
"""«Развоз» работает с накладными ERP (ответ владельца №96, docs/plans/erp-invoices-plan.md): накладная ERP дня на машину
парка — факт, её магазин в рейсе этой машины. Машина накладной — fDELIVERYCAR, без неё — экспедитор fVANAGENTID по имени в
экипаже дня (dispatch.fact_truck); рейсы под накладные — dispatch.sync_invoiced; заказы с накладной — в развозе всегда
(views._active_orders). Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_erp_invoices.py -q
"""
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp, views  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import (HOWO, _dispatch_setup, _dorder, _dp_ctx, _dp_stops, _isn, client,  # noqa: E402,F401
                                  make_snapshot)

T = fl.FleetTruck('333DN33', 'FORD', 2800.0, 16.0)   # 10.10.2026: 333DN33, водитель — экспедитор 3208
VAN = 3208
ARMEN = 'Հարությունյան  Արմեն'                        # в ERP — с двойным пробелом
D = date(2026, 10, 1)


def _pt(i):
    return 40.18 + (i % 8) * 0.004, 44.50 + (i // 8 % 8) * 0.006


def _inv(cids, code=T.car_code):
    return {c: frozenset({code}) for c in cids}


def _trucks_of(draft, cid):
    return [t.truck for t in draft.trips if cid in t.stops]


def _kg(stops, trip):
    by = {s.customer_id: s.kg for s in stops}
    return sum(by[c] for c in trip.stops)


# --- машина накладной ---

def test_fact_truck_car_van_namesake_self_and_manual():
    names = {VAN: ARMEN, 3209: 'Պետրոսյան Արամ', 3210: 'Նոր Մարդ', 7: 'Մենեջեր'}
    crew = dp.crew_index({T.car_code: ['հարությունյան արմեն', 'Կարապետյան Վարդան'], HOWO.car_code: ['Պետրոսյան Արամ', None],
                          'CAR9': ['Պետրոսյան  արամ']})
    assert dp.fact_truck(' 991AT61'.strip(), VAN, 7, names, crew, {}) == '991AT61'          # машина в накладной — главнее
    assert dp.fact_truck('', VAN, 7, names, crew, {}) == T.car_code                         # двойной пробел, регистр
    assert dp.fact_truck('', 3209, 7, names, crew, {}) == ''                               # тёзки у двух машин — ничья
    assert dp.fact_truck('', 3209, 7, names, crew, {3209: 'MAN1'}) == 'MAN1'               # … тогда ручная машина
    assert dp.fact_truck('', 3210, 7, names, crew, {3210: 'MAN1'}) == 'MAN1'               # нет в экипаже — ручная
    assert dp.fact_truck('', 7, 7, names, crew, {7: 'MAN1'}) == ''                         # менеджер везёт сам
    assert dp.fact_truck('', None, 7, names, crew, {}) == '' and dp.fact_truck('', 0, 7, names, crew, {}) == ''
    assert dp.fact_truck('', 4040, 7, names, crew, {}) == ''                               # агента нет в справочнике


def test_invoiced_trucks_only_this_day_and_fleet():
    orders = [replace(_dorder(1, 101, 100.0), shipped=D, shipped_van=VAN),
              replace(_dorder(2, 102, 100.0), shipped=D, shipped_car='991AT61'),
              replace(_dorder(3, 103, 100.0), shipped=D, shipped_car='MGR1'),            # машина не парка
              replace(_dorder(4, 104, 100.0), shipped=date(2026, 9, 30), shipped_van=VAN),  # накладная вчера
              replace(_dorder(5, 105, 100.0), shipped=D, shipped_van=4040),               # экспедитор не найден
              replace(_dorder(6, 106, 100.0), shipped=D, shipped_van=4040),
              replace(_dorder(7, 107, 100.0), shipped=D, shipped_van=1),                  # сам менеджер (agent 1)
              _dorder(8, 108, 100.0)]                                                      # накладной нет
    crew = dp.crew_index({T.car_code: [ARMEN]})
    inv, unknown = dp.invoiced_trucks(orders, D, {VAN: ARMEN, 4040: 'Անհայտ'}, crew, {}, {T.car_code, '991AT61'})
    assert inv == {_isn(1): T.car_code, _isn(2): '991AT61'}
    assert unknown == [dp.UnknownVan(4040, 'Անհայտ', (105, 106))]
    assert dp.invoiced_customers(orders, inv) == {101: frozenset({T.car_code}), 102: frozenset({'991AT61'})}
    # менеджер 1 снят фильтром (№69): его накладная — только на машине, которая уже в плане; иначе её нет нигде
    inv, unknown = dp.invoiced_trucks(orders, D, {VAN: ARMEN, 4040: 'Անհայտ'}, crew, {}, {T.car_code, '991AT61'}, off={1})
    assert inv == {} and unknown == []
    inv, unknown = dp.invoiced_trucks(orders, D, {VAN: ARMEN, 4040: 'Անհայտ'}, crew, {}, {T.car_code, '991AT61'}, off={1},
                                      planned={T.car_code})
    assert inv == {_isn(1): T.car_code} and unknown == []


def test_draft_erp_sig_roundtrip_and_old_drafts():
    raw = dp.Draft(trucks=['A'], trips=[dp.DraftTrip(1, 'A', [1])], next_id=2, erp_sig='abc').to_json()
    assert raw['erp_sig'] == 'abc' and dp.Draft.from_json(raw).erp_sig == 'abc'
    old = dp.Draft(trucks=['A'], trips=[dp.DraftTrip(1, 'A', [1])], next_id=2).to_json()
    assert 'erp_sig' not in old                                                    # нет отпечатка — ключа нет
    assert dp.Draft.from_json(old).erp_sig is None and dp.Draft.from_json({**old, 'erp_sig': 5}).erp_sig is None
    assert dp.invoiced_sig({1: {'A'}}) == dp.invoiced_sig({1: frozenset({'A'})}) != dp.invoiced_sig({1: {'B'}})


# --- рейсы под накладные ---

def test_sync_case_of_10_10():
    """10.10: у 333DN33 (2 800 кг) 2 рейса, 20 магазинов; накладных — 17: 5 из плана и 12 магазинов вне рейсов; 15 магазинов
    плана без накладной. Все 17 — в первом рейсе машины; без накладной — что не помещается, уходит в её второй рейс."""
    ctx = _dp_ctx(trucks=(T,))
    stops, _ = _dp_stops([(200 + i, _pt(i), 130.0) for i in range(32)])
    draft = dp.Draft(trucks=[T.car_code], trips=[dp.DraftTrip(1, T.car_code, list(range(200, 210))),
                                                  dp.DraftTrip(2, T.car_code, list(range(210, 220)))], next_id=3)
    invoiced = [*range(200, 205), *range(220, 232)]
    inv = _inv(invoiced)
    assert dp.sync_invoiced(ctx, stops, draft, inv) is True
    first = next(t for t in draft.trips if t.id == 1)
    assert set(invoiced) <= set(first.stops)                                       # все 17 — в первом рейсе
    assert _kg(stops, first) <= 0.9 * T.capacity_kg + 0.5                           # в пределе сборки
    assert {205, 206, 207} & set(first.stops) == set()                              # лишние без накладной — вон из него
    placed = [c for t in draft.trips for c in t.stops]
    assert len(placed) == len(set(placed))                                          # магазин — один раз
    assert set(range(210, 220)) <= set(placed)                                      # второй рейс никого не потерял
    assert draft.erp_sig == dp.invoiced_sig(inv)
    before = draft.to_json()
    assert dp.sync_invoiced(ctx, stops, draft, inv) is False                        # второй вызов — ничего
    assert draft.to_json() == before


def test_sync_moves_store_from_other_truck_unless_pinned_by_logist():
    ctx = _dp_ctx(trucks=(T, HOWO))
    stops, _ = _dp_stops([(300 + i, _pt(i), 100.0) for i in range(4)])

    def draft(pinned=False, approved=False):
        return dp.Draft(trucks=[T.car_code, HOWO.car_code], next_id=3,
                        trips=[dp.DraftTrip(1, HOWO.car_code, [300, 301], pinned or approved),
                               dp.DraftTrip(2, T.car_code, [302], approved)],
                        approved={'at': '2026-10-01T08:00:00', 'by': None, 'pinned': [1, 2]} if approved else None)
    d = draft()
    assert dp.sync_invoiced(ctx, stops, d, _inv([301])) is True
    assert _trucks_of(d, 301) == [T.car_code] and _trucks_of(d, 300) == [HOWO.car_code]
    d = draft(approved=True)                                                        # утверждение не мешает (№73)
    assert dp.sync_invoiced(ctx, stops, d, _inv([301])) is True and _trucks_of(d, 301) == [T.car_code]
    d = draft(pinned=True)                                                          # закрепил логист — не трогаем
    assert dp.sync_invoiced(ctx, stops, d, _inv([301])) is False
    assert _trucks_of(d, 301) == [HOWO.car_code]
    assert dp.erp_held(ctx, stops, d, _inv([301])) == {301}


def test_sync_follows_delivery_fact_not_plan_clock():
    """По часам плана первый рейс T и рейс HOWO уже уехали, но накладные ERP — факт: такой рейс правится. Не меняются только
    рейсы, где у терминала уже есть ход доставки (done), загруженные и закреплённые логистом."""
    ctx = _dp_ctx(trucks=(T, HOWO))
    stops, _ = _dp_stops([(400 + i, _pt(i), 100.0) for i in range(8)])

    def draft():
        return dp.Draft(trucks=[T.car_code, HOWO.car_code], next_id=4,
                        trips=[dp.DraftTrip(1, T.car_code, [400, 401, 402, 403]), dp.DraftTrip(2, T.car_code, [404, 405]),
                               dp.DraftTrip(3, HOWO.car_code, [407])])
    d = draft()
    routable = {s.customer_id: s for s in stops}
    tl = dp._timeline(ctx, d.trips, routable, dp._shares(d.trips))
    now = max(tl[1][0], tl[3][0]) + 1                                               # по плану первый рейс T и HOWO уехали
    assert dp.started_trips(ctx, stops, d, now) == {1, 3}
    assert dp.sync_invoiced(ctx, stops, d, _inv([406, 407]), now) is True
    assert {406, 407} <= set(next(t for t in d.trips if t.id == 1).stops)           # первый рейс T — с накладными
    assert 407 not in _trucks_of(d, 407)[1:] and _trucks_of(d, 407) == [T.car_code]
    # ход доставки по факту «Առաքիչ»: у первого рейса T и у рейса HOWO — эти рейсы не трогаем
    d = draft()
    done = {T.car_code: {400}, HOWO.car_code: {407}}
    trip1 = list(d.trips[0].stops)
    assert dp.sync_invoiced(ctx, stops, d, _inv([406, 407]), now, done) is True
    assert d.trips[0].stops == trip1                                                # развозит — как был
    assert 406 in next(t for t in d.trips if t.id == 2).stops                        # в первый свободный рейс T
    assert _trucks_of(d, 407) == [HOWO.car_code]                                    # чужой рейс в пути — не правим
    assert dp.erp_held(ctx, stops, d, _inv([406, 407]), done) == {407}
    assert dp.sync_invoiced(ctx, stops, d, _inv([406, 407]), now, done) is False
    # загруженный рейс (№78) — не целевой: магазин накладной — новым рейсом
    d = dp.Draft(trucks=[T.car_code], next_id=2,
                 trips=[dp.DraftTrip(1, T.car_code, [400], True, loaded={'at': '2026-10-01T08:00:00', 'by': None, 'pin': True})])
    assert dp.sync_invoiced(ctx, stops, d, _inv([401])) is True
    assert d.trips[0].stops == [400] and [t.stops for t in d.trips[1:]] == [[401]]


def test_sync_puts_invoiced_trip_first():
    """Live 10.10: после приведения у 333DN33 рейс 1 — 15 магазинов без накладной, рейс 2 — 17 с накладными. Товар накладных —
    то, что в машине сейчас: рейс с ними — первый свободный рейс машины; загруженный рейс остаётся на своём месте."""
    ctx = _dp_ctx(trucks=(T,))
    stops, _ = _dp_stops([(600 + i, _pt(i), 100.0) for i in range(6)])
    loaded = {'at': '2026-10-01T08:00:00', 'by': None, 'pin': True}
    d = dp.Draft(trucks=[T.car_code], next_id=4,
                 trips=[dp.DraftTrip(3, T.car_code, [604], True, loaded=loaded), dp.DraftTrip(1, T.car_code, [600, 601]),
                        dp.DraftTrip(2, T.car_code, [602, 603])])
    inv = _inv([602, 603])
    assert dp.sync_invoiced(ctx, stops, d, inv) is True
    assert [t.id for t in d.trips] == [3, 2, 1]                                     # с накладными — раньше без них
    assert sorted(d.trips[1].stops) == [602, 603] and sorted(d.trips[2].stops) == [600, 601]
    assert dp.sync_invoiced(ctx, stops, d, inv) is False


def test_sync_spills_invoices_over_capacity_and_adds_truck():
    ctx = _dp_ctx(trucks=(T, HOWO))
    stops, _ = _dp_stops([(500, _pt(0), 100.0), *((501 + i, _pt(i + 1), 1000.0) for i in range(3))])
    d = dp.Draft(trucks=[HOWO.car_code], next_id=3, unmanned={T.car_code: 'absent'},
                 trips=[dp.DraftTrip(1, HOWO.car_code, [500]), dp.DraftTrip(2, HOWO.car_code, [501])])
    assert dp.sync_invoiced(ctx, stops, d, _inv([501, 502, 503])) is True
    mine = [t for t in d.trips if t.truck == T.car_code]
    assert sorted(c for t in mine for c in t.stops) == [501, 502, 503]             # все накладные — у T
    assert len(mine) == 2 and all(_kg(stops, t) <= 0.9 * T.capacity_kg + 0.5 for t in mine)   # 3 т — двумя рейсами
    assert T.car_code in d.trucks and T.car_code not in d.unmanned                   # накладная: машина вышла
    assert _trucks_of(d, 500) == [HOWO.car_code]
    assert dp.sync_invoiced(ctx, stops, d, _inv([501, 502, 503])) is False


# --- страница «Развоза» ---

DAY = '2026-10-01'
OLD = date(2026, 9, 28)


@pytest.fixture(autouse=True)
def _before_day(monkeypatch):
    """Сейчас — вечер 30.09: развоз 01.10 — завтрашний день."""
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 9, 30, 18, 0))
    monkeypatch.setattr(views, '_same_day_now', lambda: datetime(2026, 9, 30, 18, 0))


def _setup(client):
    """Накладные на 01.10 у экспедитора 3208 — водителя CAR2: заказ дня 101, заказ менеджера 2 (102) и заказ прошлых дней
    104, который логист не брал «Տանել այսօր». Заказ 103 — без накладной."""
    orders = [replace(_dorder(1, 101, 400.0), shipped=D, shipped_van=VAN),
              replace(_dorder(2, 102, 300.0, agent=2), shipped=D, shipped_van=VAN),
              _dorder(3, 103, 200.0),
              replace(_dorder(6, 104, 70.0, agent=2, day=OLD), shipped=D, shipped_van=VAN)]
    _dispatch_setup(client, orders)
    state = client.application.extensions['route_optimizer']
    snap = make_snapshot()
    snap = replace(snap, agents={**snap.agents, VAN: erp.Agent(VAN, 'B208', ARMEN, False)})
    state.snapshots = SnapshotCache(lambda: snap)
    state.store.save_truck_driver('CAR2', DAY, 'հարությունյան արմեն', 'test')
    return state


def _edit(client, d, **body):
    r = client.post('/api/routes/dispatch/edit', json={'date': DAY, 'rev': d['rev'], **body})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _truck_of(d, cid):
    return {tr['car_code'] for tr in d['plan']['trucks'] for t in tr['trips'] if any(x['customer_id'] == cid for x in t['stops'])}


def test_invoiced_orders_always_in_delivery_on_their_truck(client):
    _setup(client)
    # окно «Նախորդ օրերից» — 1 рабочий день (с 29.09): заказ 28.09 с накладной на сегодня всё равно виден — факт
    assert client.post('/api/routes/dispatch/backlog-days', json={'days': 1}).status_code == 200
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['success'], d
    # заказ прошлых дней без «Տանել այսօր» — в развозе: накладная на сегодня
    assert all(_truck_of(d, c) == {'CAR2'} for c in (101, 102, 104))
    stop = next(x for tr in d['plan']['trucks'] for t in tr['trips'] for x in t['stops'] if x['customer_id'] == 104)
    assert stop['erp_truck'] == 'CAR2'
    assert 'erp_truck' not in next(x for tr in d['plan']['trucks'] for t in tr['trips'] for x in t['stops']
                                   if x['customer_id'] == 103)
    row = d['erp']['trucks'][0]
    assert (row['car_code'], row['invoiced'], row['in_trips'], row['from_erp']) == ('CAR2', 3, 3, 1)
    assert d['erp']['unknown'] == []
    # «не везём сегодня» и фильтр менеджеров накладную не отменяют
    d = _edit(client, d, action='exclude', order=_isn(1))
    assert _truck_of(d, 101) == {'CAR2'}
    d = _edit(client, d, action='agents', off=[2])
    assert _truck_of(d, 102) == {'CAR2'} and _truck_of(d, 104) == {'CAR2'}
    assert d['orders']['agents_off'] == 0                                           # снятого менеджера везём: накладная
    # правка не уводит магазин накладной с его машины
    trip = next(t for tr in d['plan']['trucks'] for t in tr['trips'] if any(x['customer_id'] == 101 for x in t['stops']))
    d = _edit(client, d, action='move', customer_id=101, from_trip=trip['id'], to_trip=None, truck='CAR1')
    assert _truck_of(d, 101) == {'CAR2'}
    status = client.get(f'/api/routes/dispatch/status?date={DAY}').get_json()
    assert status['orders']['count'] == d['orders']['count']                        # автообновление считает так же


def test_off_manager_invoice_only_on_planned_truck(client):
    """№69 «только заказы двух менеджеров»: накладная заказа снятого менеджера не тянет в план ни машину, ни магазин;
    машина в плане (отмечена логистом) — её магазин в её рейсе."""
    orders = [_dorder(1, 101, 400.0), replace(_dorder(2, 102, 300.0, agent=2), shipped=D, shipped_van=VAN)]
    _dispatch_setup(client, orders)
    state = client.application.extensions['route_optimizer']
    snap = make_snapshot()
    snap = replace(snap, agents={**snap.agents, VAN: erp.Agent(VAN, 'B208', ARMEN, False)})
    state.snapshots = SnapshotCache(lambda: snap)
    state.store.save_truck_driver('CAR2', DAY, 'Հարությունյան Արմեն', 'test')
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1'], 'agents_off': [2]}).get_json()
    assert d['success'], d
    assert 'erp' not in d and _truck_of(d, 102) == set()                            # машины CAR2 в плане нет — нет и её
    assert 'CAR2' not in dp.Draft.from_json(state.store.load_dispatch(DAY)[0]).trucks
    assert d['orders']['agents_off'] == 1
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['success'], d
    assert _truck_of(d, 102) == {'CAR2'} and d['erp']['trucks'][0]['car_code'] == 'CAR2'
    assert d['orders']['agents_off'] == 0


def test_open_day_syncs_stored_plan_and_unknown_van(client):
    state = _setup(client)
    d = client.post('/api/routes/dispatch/build', json={'date': DAY, 'trucks': ['CAR1', 'CAR2']}).get_json()
    # план сохранён до накладных (магазины 101 и 102 — у CAR1): открытие дня приводит его к ERP
    raw, rev = state.store.load_dispatch(DAY)
    plan = dp.Draft.from_json(raw)
    for t in plan.trips:
        t.stops = [c for c in t.stops if c not in (101, 102, 104)]
    plan.trips = [t for t in plan.trips if t.stops] + [dp.DraftTrip(plan.next_id, 'CAR1', [101, 102])]
    plan.next_id += 1
    plan.erp_sig = None
    assert state.store.save_dispatch(DAY, plan.to_json(), 'test', expected_rev=rev) is not None
    d = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    assert all(_truck_of(d, c) == {'CAR2'} for c in (101, 102, 104))
    saved = dp.Draft.from_json(state.store.load_dispatch(DAY)[0])
    assert saved.erp_sig is not None and all(_trucks_of(saved, c) == ['CAR2'] for c in (101, 102, 104))
    rev = state.store.load_dispatch(DAY)[1]
    client.get(f'/api/routes/dispatch?date={DAY}')
    assert state.store.load_dispatch(DAY)[1] == rev                                 # второе открытие — без записи
    # экспедитор не в экипаже дня — машина неизвестна: на странице отдельно
    state.store.save_truck_driver('CAR2', DAY, 'Ուրիշ Մարդ', 'test')
    d = client.get(f'/api/routes/dispatch?date={DAY}').get_json()
    assert d['erp']['trucks'] == [] and d['erp']['unknown'] == [{'van_agent_id': VAN, 'name': ARMEN, 'customers': 3}]


def test_page_has_erp_banner_and_stop_badge():
    page = (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert 'id="dpErp"' in page and "routes_dispatch.js') }}?v=115" in page and "routes_dispatch.css') }}?v=72" in page
    assert 'function renderErp()' in js and 'ERP ապրանքագրեր' in js and 'մեքենան հայտնի չէ' in js
    assert "'ERP ապրանքագիր'" in js and "'ապրանքագիր չկա'" in js and 'stop.erp_truck' in js
    assert 'textContent = text' in js.split('function renderErp()')[1].split('function ')[0]   # имена из ERP — только текстом

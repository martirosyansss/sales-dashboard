# -*- coding: utf-8 -*-
"""«Развоз»: совет карточки «Չի տեղավորվել» (ответ владельца №54, dispatch._advice → plan['advice']) и чат AI только по
плану на экране (views._seen_error / _seen_stale: план или настройки изменились после открытия страницы — 409 stale без
вызова модели). Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client), модель не вызывается.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_advice.py -q
"""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import ai_chat, views  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, TN,  # noqa: E402,F401
                                  _dispatch_setup, _dorder, _dp_stops, _info, _no_road_map, client)

ZONE = tuple((lat, lon) for lat, lon in st.DEFAULT_SETTINGS['center_zone'])
CENTER = [(201, (40.1777, 44.5126), 300.0), (202, (40.1840, 44.5150), 300.0), (203, (40.1810, 44.5200), 300.0)]
JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0, center_ok=True)
ISUZU = fl.FleetTruck('120CC12', 'ISUZU', 3000.0, 14.0, center_ok=True)
GAZ = fl.FleetTruck('777AA77', 'GAZ', 3500.0, 9.0)          # расход меньше всех, но в центр не въезжает


def _ctx(trucks, tn=TN, zone=(), overtime=None, access=None):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, tn, 9 * 60,
                         overtime_minutes=overtime, center_zone=zone, vehicle_access=access or {})


def _one_trip(code, cids, **kw):
    return dp.Draft(trucks=[code], trips=[dp.DraftTrip(1, code, list(cids))], next_id=2, **kw)


def _minutes(ctx, stops, draft):
    """Минуты работы машины единственного рейса — от них строим «короткий день» (рейс опаздывает на минуту)."""
    return dp.plan_view(ctx, stops, draft, _info)['trucks'][0]['minutes']


def _add(t, for_center, need_kg):
    return {'rebuild': [], 'add': {'car_code': t.car_code, 'name': t.name, 'capacity_kg': t.capacity_kg, 'l100': t.l100,
                                   'center_ok': t.center_ok, 'for_center': for_center, 'need_kg': need_kg},
            'pinned_late': [], 'no_free': False}


# ============================== совет по плану (plan_view) ==============================

def test_advice_none_when_everything_fits():
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((FORD, HOWO, GAZ))
    view = dp.plan_view(ctx, stops, dp.build(ctx, stops, None, [FORD.car_code, HOWO.car_code], 'now'), _info)
    assert view['unassigned'] == [] and not any(t['over_time'] for tr in view['trucks'] for t in tr['trips'])
    assert view['advice'] is None


def test_advice_rebuild_when_selected_truck_idle_next_to_late_trip():
    """01.10 у владельца: черновик собран при других настройках — рейс FORD опаздывает, а отмеченная HOWO без рейсов.
    Совет — пересобрать (не «отметьте ещё машину» и не свободная GAZ); свежая сборка теми же машинами — совета нет."""
    stops, _ = _dp_stops(EAST)
    old = _one_trip(FORD.car_code, [101, 102, 103])
    old.trucks = [FORD.car_code, HOWO.car_code]
    m = _minutes(_ctx((FORD,)), stops, old)
    ctx = _ctx((FORD, HOWO, GAZ), tn=replace(TN, work_minutes=m - 1.0))      # конец дня стал раньше — рейс опаздывает
    view = dp.plan_view(ctx, stops, old, _info)
    assert [t['over_time'] for tr in view['trucks'] for t in tr['trips']] == [True]
    assert view['advice'] == {'rebuild': [HOWO.car_code], 'add': None, 'pinned_late': [], 'no_free': False}
    fresh = dp.build(ctx, stops, old, old.trucks, 'now')
    after = dp.plan_view(ctx, stops, fresh, _info)
    assert after['unassigned'] == [] and after['advice'] is None, after['advice']


def test_advice_add_center_truck_for_late_central_trip():
    """Опаздывает рейс машины с правом въезда, в нём точки центра — предлагается свободная машина с правом въезда
    (ISUZU), хотя расход меньше у GAZ; те же точки вне центра — GAZ."""
    stops, _ = _dp_stops(CENTER)
    draft = _one_trip(JAC.car_code, [201, 202, 203])
    m = _minutes(_ctx((JAC,), zone=ZONE), stops, draft)
    ctx = _ctx((JAC, ISUZU, GAZ), tn=replace(TN, work_minutes=m - 1.0), zone=ZONE)
    view = dp.plan_view(ctx, stops, draft, _info)
    trip = view['trucks'][0]['trips'][0]
    assert trip['over_time'] and all(s['center'] for s in trip['stops']) and trip['kg'] == 900
    assert view['advice'] == _add(ISUZU, True, 900)

    stops, _ = _dp_stops(EAST)
    draft = _one_trip(JAC.car_code, [101, 102, 103])
    m = _minutes(_ctx((JAC,), zone=ZONE), stops, draft)
    ctx = _ctx((JAC, ISUZU, GAZ), tn=replace(TN, work_minutes=m - 1.0), zone=ZONE)
    view = dp.plan_view(ctx, stops, draft, _info)
    assert view['trucks'][0]['trips'][0]['over_time'] and view['advice'] == _add(GAZ, False, 600)


def test_advice_accepted_overtime_is_not_a_problem():
    """«Везти после конца дня» принято: рейс позже конца дня, но раньше предела — не «не поместилось», совета нет;
    не принято — тот же рейс опаздывает, свободная HOWO предлагается."""
    stops, _ = _dp_stops(EAST)
    draft = _one_trip(FORD.car_code, [101, 102, 103], overtime_ok=True)
    m = _minutes(_ctx((FORD,)), stops, draft)
    ctx = _ctx((FORD, HOWO), tn=replace(TN, work_minutes=m - 10.0), overtime=m + 60.0)
    view = dp.plan_view(ctx, stops, draft, _info)
    trip = view['trucks'][0]['trips'][0]
    assert trip['late'] and not trip['over_time'] and view['unassigned'] == []
    assert view['advice'] is None
    view = dp.plan_view(ctx, stops, replace(draft, overtime_ok=False), _info)
    assert view['trucks'][0]['trips'][0]['over_time'] and view['advice'] == _add(HOWO, False, 600)


# ============================== выбор машины (_advice) ==============================

def _late(truck, kg, center=False, pinned=False, tid=1, cid=1):
    return {'id': tid, 'truck': truck, 'over_time': True, 'pinned': pinned, 'kg': kg,
            'stops': [{'customer_id': cid, 'center': center}]}


def _left(kg, no_room=True, no_center=False, cid=900, no_vehicle=False):
    return {'customer_id': cid, 'kg': kg, 'no_room': no_room, 'no_center': no_center, 'no_vehicle': no_vehicle}


def test_advice_pick_by_capacity_then_fuel():
    def truck(code, cap, l100, center_ok=False):
        return fl.FleetTruck(code, None, cap, l100, center_ok=center_ok)
    sel = truck('S', 3500.0, 16.0)
    ctx = _ctx((sel, truck('A', 2000.0, 10.0), truck('B', 5000.0, 20.0), truck('C', 5000.0, 20.0),
                truck('D', 3000.0, 12.0), truck('AE', 4500.0, 20.0)))
    draft = dp.Draft(trucks=['S'])

    def pick(trips, left=(), c=ctx):
        return dp._advice(c, draft, trips, list(left))['add']['car_code']
    assert pick([_late('S', 1500)]) == 'A'                  # берут все — меньший расход
    assert pick([_late('S', 2500)]) == 'D'                  # A не берёт; из остальных меньший расход
    assert pick([_late('S', 3000)]) == 'D'                  # ровно тоннаж — берёт
    assert pick([_late('S', 4000)]) == 'B'                  # B, C, AE — расход равный: вместительнее (B, C), затем код
    assert pick([_late('S', 9000)]) == 'B'                  # не берёт никто — самая вместительная, затем код
    assert pick([_late('S', 1000), _late('S', 2500)]) == 'D'                 # груз — самый тяжёлый опаздывающий рейс
    assert pick([_late('S', 1000)], [_left(1500), _left(1500)]) == 'D'      # или все магазины вне рейсов вместе
    assert dp._advice(ctx, draft, [], [_left(1500, no_room=False)]) is None  # «просто не в рейсе» — не беда
    big = _ctx((sel, truck('X', 5000.0, 22.0), truck('Y', 5000.0, 18.0), truck('Z', 4000.0, 9.0)))
    assert pick([_late('S', 9000)], c=big) == 'Y'          # не берёт никто: самые вместительные — меньший расход


def test_advice_need_kg_tells_whole_or_part():
    """need_kg — груз, под который выбрана машина (самый тяжёлый опаздывающий рейс или все магазины вне рейсов вместе):
    тоннаж не меньше — страница пишет «возьмёт этот груз», меньше — «возьмёт часть груза»."""
    sel = fl.FleetTruck('S', None, 3500.0, 16.0)
    small = fl.FleetTruck('M', None, 2500.0, 12.0)
    ctx = _ctx((sel, small))
    draft = dp.Draft(trucks=['S'])
    whole = dp._advice(ctx, draft, [_late('S', 900), _late('S', 2400)], [_left(1000)])['add']
    assert whole['need_kg'] == 2400 and isinstance(whole['need_kg'], int) and whole['capacity_kg'] >= whole['need_kg']
    assert dp._advice(ctx, draft, [_late('S', 2500)], [])['add']['need_kg'] == 2500          # ровно тоннаж — целиком
    part = dp._advice(ctx, draft, [_late('S', 900)], [_left(1500), _left(1200)])['add']
    assert part['need_kg'] == 2700 and part['capacity_kg'] < part['need_kg']                  # возьмёт часть


def test_advice_center_need_and_fallback():
    cen = fl.FleetTruck('C1', None, 2000.0, 14.0, center_ok=True)
    cheap = fl.FleetTruck('G1', None, 3500.0, 9.0)
    sel = fl.FleetTruck('S', None, 3500.0, 16.0, center_ok=True)
    draft = dp.Draft(trucks=['S'])
    ctx = _ctx((sel, cen, cheap))
    assert dp._advice(ctx, draft, [], [_left(800, no_room=False, no_center=True)]) == {
        'rebuild': [], 'add': {'car_code': 'C1', 'name': None, 'capacity_kg': 2000.0, 'l100': 14.0, 'center_ok': True,
                               'for_center': True, 'need_kg': 800}, 'pinned_late': [], 'no_free': False}
    assert dp._advice(ctx, draft, [_late('S', 800, center=True)], [])['add']['car_code'] == 'C1'
    assert dp._advice(ctx, draft, [_late('S', 800)], [])['add']['car_code'] == 'G1'   # рейс без точек центра
    plain = dp.Draft(trucks=['G1'])                                                   # опаздывает машина без права въезда
    assert dp._advice(_ctx((cheap, cen, fl.FleetTruck('G2', None, 3500.0, 8.0))), plain,
                      [_late('G1', 800, center=True)], [])['add']['car_code'] == 'G2'
    only_plain = _ctx((sel, cheap))                                                   # с правом въезда свободных нет
    assert dp._advice(only_plain, draft, [], [_left(800, no_room=False, no_center=True)])['add'] == {
        'car_code': 'G1', 'name': None, 'capacity_kg': 3500.0, 'l100': 9.0, 'center_ok': False, 'for_center': False,
        'need_kg': 800}


def test_advice_rebuild_and_no_free_trucks():
    ctx = _ctx((FORD, HOWO, GAZ))
    # отмеченные без рейсов — по коду; отмеченная, но не готовая к расчёту машина — не «свободна»
    draft = dp.Draft(trucks=[HOWO.car_code, 'GHOST', GAZ.car_code, FORD.car_code])
    assert dp._advice(ctx, draft, [_late(FORD.car_code, 900)], []) == {'rebuild': [GAZ.car_code, HOWO.car_code],
                                                                         'add': None, 'pinned_late': [], 'no_free': False}
    # магазины вне рейсов без опаздывающих рейсов — не пересборка (свежая сборка их уже не поместила)
    assert dp._advice(ctx, draft, [], [_left(500)]) == {'rebuild': [], 'add': None, 'pinned_late': [], 'no_free': True}
    lone = dp.Draft(trucks=[FORD.car_code, 'GHOST'])
    assert dp._advice(ctx, lone, [_late(FORD.car_code, 900)], [])['add']['car_code'] == GAZ.car_code
    # все готовые машины отмечены и заняты — предложить нечего
    busy = dp.Draft(trucks=[FORD.car_code, HOWO.car_code, GAZ.car_code])
    trips = [_late(FORD.car_code, 900), {**_late(HOWO.car_code, 300), 'over_time': False},
             {**_late(GAZ.car_code, 300), 'over_time': False}]
    assert dp._advice(ctx, busy, trips, [_left(400)]) == {'rebuild': [], 'add': None, 'pinned_late': [], 'no_free': True}
    assert dp._advice(ctx, busy, [{**t, 'over_time': False} for t in trips], []) is None


def test_advice_pinned_late_trip_is_not_rebuild():
    """Опаздывает закреплённый рейс, отмеченная HOWO свободна: пересборка закреплённый рейс не меняет — совет не
    «пересобрать» (иначе он навсегда), а pinned_late; сборка теми же машинами и правда оставляет рейс как был."""
    stops, _ = _dp_stops(EAST)
    old = dp.Draft(trucks=[FORD.car_code, HOWO.car_code], next_id=2,
                   trips=[dp.DraftTrip(1, FORD.car_code, [101, 102, 103], pinned=True)])
    m = _minutes(_ctx((FORD,)), stops, old)
    ctx = _ctx((FORD, HOWO, GAZ), tn=replace(TN, work_minutes=m - 1.0))
    view = dp.plan_view(ctx, stops, old, _info)
    assert view['trucks'][0]['trips'][0]['over_time'] and view['trucks'][0]['trips'][0]['pinned']
    assert view['advice'] == {'rebuild': [], 'add': None, 'pinned_late': [1], 'no_free': False}
    again = dp.plan_view(ctx, stops, dp.build(ctx, stops, old, old.trucks, 'now'), _info)
    assert again['advice'] == {'rebuild': [], 'add': None, 'pinned_late': [1], 'no_free': False}


def test_advice_pinned_late_with_other_problems():
    """Закреплённый опаздывающий рейс не мешает совету по остальным и не входит в груз need_kg."""
    ctx = _ctx((FORD, HOWO, GAZ))
    draft = dp.Draft(trucks=[FORD.car_code, HOWO.car_code])
    pinned = _late(FORD.car_code, 3000, pinned=True, tid=7)
    assert dp._advice(ctx, draft, [pinned, _late(FORD.car_code, 900, tid=8)], []) == {
        'rebuild': [HOWO.car_code], 'add': None, 'pinned_late': [7], 'no_free': False}
    busy = [pinned, {**_late(HOWO.car_code, 100, tid=9), 'over_time': False}]
    got = dp._advice(ctx, draft, busy, [_left(400)])
    assert got['pinned_late'] == [7] and got['add']['car_code'] == GAZ.car_code and got['add']['need_kg'] == 400


def test_advice_no_free_only_beyond_pinned_trips():
    """no_free («все машины уже отмечены») — только когда беда есть сверх закреплённых рейсов и свободной машины нет;
    опаздывают одни закреплённые — False, есть ли свободные машины или нет (страница пишет прежние тексты)."""
    ctx = _ctx((FORD, HOWO, GAZ))
    pinned = _late(FORD.car_code, 900, pinned=True, tid=7)
    other = [{**_late(HOWO.car_code, 300, tid=8), 'over_time': False}, {**_late(GAZ.car_code, 300, tid=9), 'over_time': False}]
    assert dp._advice(ctx, dp.Draft(trucks=[FORD.car_code]), [pinned], []) == {
        'rebuild': [], 'add': None, 'pinned_late': [7], 'no_free': False}                    # свободные есть
    everyone = dp.Draft(trucks=[FORD.car_code, HOWO.car_code, GAZ.car_code])
    assert dp._advice(ctx, everyone, [pinned, *other], []) == {
        'rebuild': [], 'add': None, 'pinned_late': [7], 'no_free': False}                    # свободных нет, но беда — только закреплённый
    assert dp._advice(ctx, everyone, [pinned, *other], [_left(300)]) == {
        'rebuild': [], 'add': None, 'pinned_late': [7], 'no_free': True}                     # и магазин вне рейсов — добавить нечего
    late = [pinned, {**other[0], 'over_time': True}, other[1]]
    assert dp._advice(ctx, everyone, late, [])['no_free'] is True


def test_advice_truck_must_be_allowed_at_every_problem_store():
    """Две проблемные точки, свободная A (дешевле) допущена только в одну — выбирается B, допущенная в обе."""
    a, b = fl.FleetTruck('A', None, 3000.0, 9.0), fl.FleetTruck('B', None, 3000.0, 14.0)
    sel = fl.FleetTruck('S', None, 3500.0, 16.0)
    ctx = _ctx((sel, a, b), access={501: VehicleAccess('allow', ('A', 'B', 'S')), 502: VehicleAccess('deny', ('A',))})
    draft = dp.Draft(trucks=['S'])
    assert dp._advice(ctx, draft, [], [_left(400, cid=501), _left(400, cid=502)])['add']['car_code'] == 'B'
    assert dp._advice(ctx, draft, [_late('S', 400, cid=501)], [_left(400, cid=502)])['add']['car_code'] == 'B'
    assert dp._advice(ctx, draft, [], [_left(400, cid=501)])['add']['car_code'] == 'A'      # одна точка — A допущена


def test_advice_prefers_truck_allowed_at_problem_stores():
    """Допуск машин магазина (VehicleAccess): из свободных — допущенные ко всем проблемным магазинам (вне рейсов и в
    опаздывающих рейсах), таких нет — как раньше. Магазины без допущенной машины среди отмеченных (no_vehicle) — в своей
    карточке страницы: ни в совете, ни в грузе."""
    a, b = fl.FleetTruck('A', None, 3000.0, 9.0), fl.FleetTruck('B', None, 3000.0, 14.0)
    sel = fl.FleetTruck('S', None, 3500.0, 16.0)
    draft = dp.Draft(trucks=['S'])
    only_b = _ctx((sel, a, b), access={501: VehicleAccess('allow', ('B', 'S'))})
    assert dp._advice(only_b, draft, [], [_left(800, cid=501)])['add']['car_code'] == 'B'     # A дешевле, но не допущена
    assert dp._advice(only_b, draft, [_late('S', 800, cid=501)], [])['add']['car_code'] == 'B'
    nobody = _ctx((sel, a, b), access={501: VehicleAccess('allow', ('S',))})
    assert dp._advice(nobody, draft, [], [_left(800, cid=501)])['add']['car_code'] == 'A'     # допущенных нет
    assert dp._advice(only_b, draft, [], [_left(800, cid=501, no_vehicle=True)]) is None
    got = dp._advice(only_b, draft, [_late('S', 500)], [_left(5000, cid=501, no_vehicle=True)])['add']
    assert got['car_code'] == 'A' and got['need_kg'] == 500


# ============================== чат AI — только по плану на экране ==============================

class _Ask:
    """Подмена ai_chat.ask: модель не вызывается, запоминаем ответ дня, с которым спросили бы."""

    def __init__(self):
        self.bodies = []

    def __call__(self, body, question, history, focus=None):
        self.bodies.append(body)
        return {'answer': 'Պատասխան', 'model': 'm', 'refused': False, 'truncated': False}


@pytest.fixture
def asked(client, monkeypatch):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    fake = _Ask()
    monkeypatch.setattr(ai_chat, 'ask', fake)
    monkeypatch.setattr(ai_chat, '_get_client', lambda: pytest.fail('модель не должна вызываться'))
    return fake, r.get_json()


def _seen(day):
    """Что страница отправляет вместе с вопросом (aiAsk): рейсы плана на экране."""
    return [[t['id'], t['return'], t['over_time']] for tr in day['plan']['trucks'] for t in tr['trips']]


def _shift(text, minutes):
    h, m = map(int, text.split(':'))
    t = h * 60 + m + minutes
    return f'{t // 60:02d}:{t % 60:02d}'


def _post(client, **kw):
    return client.post('/api/routes/dispatch/ask', json={'date': '2026-10-01', 'question': 'Ո՞ր մեքենան', **kw})


def test_ask_stale_plan_is_409_without_model(client, asked):
    fake, day = asked
    rev, seen = day['rev'], _seen(day)
    (tid, ret, late), rest = seen[0], seen[1:]
    for body in ({'rev': rev + 1, 'seen': seen},                        # план пересобрали в другой вкладке
                 {'rev': rev - 1},
                 {'rev': rev, 'seen': rest},                            # другие рейсы
                 {'rev': rev, 'seen': seen + [[999, '12:00', False]]},
                 {'rev': rev, 'seen': [[tid, ret, not late], *rest]},   # другая пометка «опаздывает»
                 {'rev': rev, 'seen': [[tid, _shift(ret, 6), late], *rest]},    # возвращение уехало больше 5 минут
                 {'rev': rev, 'seen': [[tid, _shift(ret, -6), late], *rest]},
                 {'rev': rev, 'seen': [[tid, None, late], *rest]},
                 {'rev': rev, 'seen': None}):                           # на странице плана ещё не было
        r = _post(client, **body)
        assert r.status_code == 409, (body, r.get_json())
        assert r.get_json() == {'success': False, 'error': views.AI_STALE, 'stale': True}
    assert fake.bodies == []


def test_ask_same_plan_passes_through(client, asked, monkeypatch):
    fake, day = asked
    real, calls = views._dispatch_body, []
    monkeypatch.setattr(views, '_dispatch_body', lambda dd: calls.append(dd.day) or real(dd))
    rev, seen = day['rev'], _seen(day)
    near = [[tid, _shift(ret, 5 if i % 2 else -5), late] for i, (tid, ret, late) in enumerate(seen)]
    for body in ({'rev': rev, 'seen': seen},
                 {'rev': rev, 'seen': near},           # пересчёт сдвинул время в пределах 5 минут — тот же план
                 {'rev': rev, 'seen': list(reversed(seen))},
                 {}):                                  # страница прежней версии — без проверки
        r = _post(client, **body)
        assert r.status_code == 200 and r.get_json()['answer'] == 'Պատասխան', (body, r.get_json())
    assert len(fake.bodies) == 4 and len(calls) == 4   # ответ дня считается один раз на вопрос — его же видит модель
    assert fake.bodies[0]['rev'] == rev and _seen(fake.bodies[0]) == seen


@pytest.mark.parametrize('extra', [
    {'seen': 'x'}, {'seen': {}}, {'seen': [[1, '10:00']]}, {'seen': [[True, '10:00', False]]},
    {'seen': [[1.0, '10:00', False]]}, {'seen': [[1, '10:00', 'да']]}, {'seen': [[1, '7:05', False]]},
    {'seen': [[1, 600, False]]}, {'seen': [[1, '10:00 (+x)', False]]}, {'seen': [[1, None, False]] * 501},
    {'seen': [{'id': 1}]}, {'rev': '3'}, {'rev': True}, {'rev': None}, {'rev': 1.5},
])
def test_ask_bad_page_plan_is_400(client, asked, extra):
    fake, day = asked
    r = _post(client, **{'rev': day['rev'], 'seen': _seen(day), **extra})
    assert r.status_code == 400 and r.get_json()['success'] is False, r.get_json()
    assert fake.bodies == []


def test_return_minutes_after_midnight():
    assert views._return_min('17:32') == 17 * 60 + 32 and views._return_min(None) is None
    assert views._return_min('04:06 (+1)') == 24 * 60 + 4 * 60 + 6 == 1686


def test_ask_page_plan_after_midnight_is_valid(client, asked):
    """Возвращение после полуночи («04:06 (+1)», dispatch._hhmm) — допустимый формат: не 400, а сверка с планом."""
    fake, day = asked
    (tid, ret, late), *rest = _seen(day)
    r = _post(client, rev=day['rev'], seen=[[tid, '04:06 (+1)', late], *rest])
    assert r.status_code == 409 and fake.bodies == []


def test_page_translates_new_server_texts(client, asked):
    """Страница армянская (№31): тексты «план устарел» и ошибок rev / seen есть в словаре SERVER_HY."""
    texts = {views.AI_STALE}
    for extra in ({'rev': 'x'}, {'seen': 'x'}):
        r = _post(client, **extra)
        assert r.status_code == 400
        texts.add(r.get_json()['error'])
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    assert len(texts) == 3 and [t for t in texts if "'" + t + "':" not in js] == []

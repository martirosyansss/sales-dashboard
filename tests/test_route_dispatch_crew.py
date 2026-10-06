# -*- coding: utf-8 -*-
"""«Развоз»: водителей меньше, чем машин (ответ владельца №77, docs/plans/driver-count-plan.md) — отсутствие водителей
(store.driver_absence, схема 23), рассадка при сборке (dispatch.seating, build_crewed), кто ведёт машину (crew_view),
API «Վարորդներ» и Բեռնագիր с фактическим водителем. Синтетические данные, без ERP; база «Маршрутов» — временная.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_crew.py -q
"""
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.vehicle_access import VehicleAccess  # noqa: E402
from test_route_optimizer import (DP_DAY, DP_DEPOT, DP_NORMS, EAST, FORD, HOWO, TN, WEST,  # noqa: E402,F401
                                  _dispatch_setup, _dorder, _dp_stops, _info, _no_road_map, client)

FORD2 = fl.FleetTruck('504CQ61', 'FORD', 3500.0, 16.0)      # как FORD, другой код
JAC = fl.FleetTruck('475DD61', 'JAC', 2200.0, 12.0)


def _ctx(trucks, access=None):
    return dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in trucks}, DP_NORMS, TN, 9 * 60,
                         vehicle_access=access or {})


def _crew(own, absent=(), manual=()):
    return dp.Crew(dict(own), frozenset(manual), frozenset(absent))


# ============================== кто за рулём без пересадок ==============================

def test_seating_own_absent_free_and_no_driver():
    crew = _crew({'A': 'Արամ', 'B': 'Կարեն', 'C': 'Լևոն', 'D': 'Գոռ'}, absent={'Կարեն'})
    # A — свой водитель; B — водитель не вышел; E — водителя нет в «Վարորդ» (едет, как до №77); C не отмечена — Լևոն свободен
    assert dp.seating(crew, ['A', 'B', 'E', 'D']) == ({'A': 'Արամ', 'D': 'Գոռ'}, {'B': 'absent'}, ['Լևոն'])
    assert dp.seating(_crew({}), ['A', 'B']) == ({}, {}, [])


def test_seating_one_person_one_truck_logist_choice_first():
    """Логист поставил Արամ на B на этот день (подмена), а постоянно Արամ — на A: ведёт B, A — без водителя: «ведёт
    другую машину» ('busy'), а не «не вышел»."""
    crew = _crew({'A': 'Արամ', 'B': 'Արամ'}, manual={'B'})
    assert dp.seating(crew, ['A', 'B']) == ({'B': 'Արամ'}, {'A': 'busy'}, [])
    assert dp.seating(_crew({'A': 'Արամ', 'B': 'Արամ'}), ['A', 'B']) == ({'A': 'Արամ'}, {'B': 'busy'}, [])   # по коду


# ============================== сборка с водителями дня ==============================

def test_no_absence_plan_is_byte_identical_to_build():
    """Golden: никого нет в отсутствующих — сборка и план те же, что у build, до байта (черновик без новых ключей)."""
    stops, _ = _dp_stops(EAST + WEST)
    ctx = _ctx((HOWO, FORD, JAC))
    codes = [HOWO.car_code, FORD.car_code, JAC.car_code]
    want = dp.build(ctx, stops, None, codes, 'now')
    for crew in (_crew({}), _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}),
                 _crew({HOWO.car_code: 'Արամ', 'X': 'Լևոն'}, absent={'Լևոն'})):    # не вышел тот, чья машина не отмечена
        got = dp.build_crewed(ctx, stops, None, codes, 'now', crew)
        raw = got.to_json()
        raw.pop('absent', None)
        assert json.dumps(raw, sort_keys=True) == json.dumps(want.to_json(), sort_keys=True)
        assert json.dumps(dp.plan_view(ctx, stops, got, _info), sort_keys=True) == \
            json.dumps(dp.plan_view(ctx, stops, want, _info), sort_keys=True)
        assert (got.seats, got.unmanned) == ({}, {})
    assert 'absent' not in dp.build_crewed(ctx, stops, None, codes, 'now', _crew({})).to_json()


def test_free_driver_takes_truck_of_absent_driver():
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now', crew)
    assert d.trucks == sorted([HOWO.car_code, FORD.car_code]) and d.seats == {FORD.car_code: 'Լևոն'} and d.unmanned == {}
    assert d.absent == ['Կարեն']


def test_more_trucks_than_drivers_picks_cheapest_by_day_cost():
    """Двух машин без водителя, свободный водитель один: груз (600 кг) везёт любая — берётся та, с которой день дешевле
    (FORD 16 л/100 против HOWO 30 л/100), HOWO — без водителя."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, JAC))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Արամ', 'Կարեն'})
    timing = {}
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew, timing)
    assert d.trucks == sorted([FORD.car_code, JAC.car_code]) and d.seats == {FORD.car_code: 'Լևոն'}
    assert d.unmanned == {HOWO.car_code: 'absent'} and timing['trials'] >= 2
    assert {t.truck for t in d.trips} <= set(d.trucks)


def test_busy_driver_truck_reason():
    """Логист поставил водителя FORD на HOWO на этот день: FORD — без водителя, причина «ведёт другую машину» ('busy')."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    crew = _crew({HOWO.car_code: 'Կարեն', FORD.car_code: 'Կարեն'}, manual={HOWO.car_code})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now', crew)
    assert d.trucks == [HOWO.car_code] and d.unmanned == {FORD.car_code: 'busy'} and d.seats == {}


def test_greedy_picks_two_of_three(monkeypatch):
    """Жадный выбор (наборов больше SEAT_EXHAUSTIVE_MAX) при двух свободных водителях из трёх машин без водителя."""
    monkeypatch.setattr(dp, 'SEAT_EXHAUSTIVE_MAX', 0)
    stops, _ = _dp_stops(EAST + WEST)
    ctx = _ctx((HOWO, FORD, JAC))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', JAC.car_code: 'Գոռ', 'X': 'Լևոն', 'Y': 'Սոս'},
                 absent={'Արամ', 'Կարեն', 'Գոռ'})
    timing = {}
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew, timing)
    assert len(d.seats) == 2 and sorted(d.seats.values()) == ['Լևոն', 'Սոս'] and len(d.unmanned) == 1
    assert set(d.trucks) == set(d.seats) and HOWO.car_code in d.unmanned       # дорогая машина — без водителя
    assert timing['trials'] == 3 + 2       # шаг 1 — три машины, шаг 2 — две оставшиеся


def test_two_rounds_of_swaps():
    """Две машины со своими водителями, две без них, магазин 101 принимает только FORD, 104 — только FORD2: каждая пересадка
    увозит ещё магазин — за два хода оба водителя пересаживаются ('moved')."""
    howo2 = fl.FleetTruck('991AT62', 'HOWO', 10000.0, 30.0)
    stops, _ = _dp_stops(EAST + WEST)
    ctx = _ctx((HOWO, howo2, FORD, FORD2), access={101: VehicleAccess('allow', (FORD.car_code,)),
                                                  104: VehicleAccess('allow', (FORD2.car_code,))})
    crew = _crew({HOWO.car_code: 'Արամ', howo2.car_code: 'Գոռ', FORD.car_code: 'Կարեն', FORD2.car_code: 'Լևոն'},
                 absent={'Կարեն', 'Լևոն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, howo2.car_code, FORD.car_code, FORD2.car_code], 'now', crew)
    assert sorted(d.trucks) == sorted([FORD.car_code, FORD2.car_code])
    assert d.unmanned == {HOWO.car_code: 'moved', howo2.car_code: 'moved'} and sorted(d.seats.values()) == ['Արամ', 'Գոռ']
    assert not (d.no_room | d.no_vehicle)


def test_trial_budget_keeps_best_found(monkeypatch):
    """Проб не больше SEAT_TRIALS_MAX: кончились — машины выбора по коду, пересадок без пробы нет; план собран."""
    monkeypatch.setattr(dp, 'SEAT_TRIALS_MAX', 1)
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, JAC))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Արամ', 'Կարեն'})
    timing = {}
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew, timing)
    assert timing['trials'] == 1 and len(d.seats) == 1 and len(d.unmanned) == 1 and d.trips
    monkeypatch.setattr(dp, 'SEAT_TRIALS_MAX', 0)
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew, timing)
    first = min(HOWO.car_code, FORD.car_code)
    assert timing['trials'] == 0 and d.seats == {first: 'Լևոն'} and d.trips


def test_more_trucks_than_drivers_needs_the_allowed_truck():
    """Магазин 101 принимает только HOWO: дешевле FORD, но тогда 101 не увезти — сборка берёт HOWO (сначала всё увезти)."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, JAC), access={101: VehicleAccess('allow', (HOWO.car_code,))})
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Արամ', 'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew)
    assert d.seats == {HOWO.car_code: 'Լևոն'} and d.unmanned == {FORD.car_code: 'absent'}
    assert not (d.no_room | d.no_vehicle)


def test_greedy_when_too_many_sets(monkeypatch):
    monkeypatch.setattr(dp, 'SEAT_EXHAUSTIVE_MAX', 0)
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, JAC))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Արամ', 'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, JAC.car_code], 'now', crew)
    assert d.seats == {FORD.car_code: 'Լևոն'} and d.unmanned == {HOWO.car_code: 'absent'}


def test_identical_trucks_tried_once():
    """Две одинаковые машины без водителя, свободный один: набор один — без пробы, машина — с меньшим кодом; пробы — только
    пересадки Արամ с HOWO на одинаковую FORD (одна пара, а не две) и день, с которым её сравнивают."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, FORD2))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', FORD2.car_code: 'Գոռ', 'OFF': 'Լևոն'},
                 absent={'Կարեն', 'Գոռ'})
    timing = {}
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code, FORD2.car_code], 'now', crew, timing)
    first, second = sorted([FORD.car_code, FORD2.car_code])
    assert d.seats == {first: 'Լևոն'} and d.unmanned == {second: 'absent'}
    assert timing['trials'] == 2


def test_no_free_driver_truck_stays_home():
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now',
                        _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Արամ'}, manual={FORD.car_code}))
    assert d.trucks == [FORD.car_code] and d.unmanned == {HOWO.car_code: 'absent'} and d.seats == {}
    assert {t.truck for t in d.trips} == {FORD.car_code}


def test_swap_off_own_truck_only_when_clearly_cheaper():
    """Свой водитель HOWO (30 л/100) вышел, водитель FORD (16 л/100) — нет: груз везёт любая, день на FORD заметно дешевле —
    водитель HOWO пересаживается на FORD ('moved'). Порог выше выигрыша — остаётся на своей."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now', crew)
    assert d.trucks == [FORD.car_code] and d.seats == {FORD.car_code: 'Արամ'} and d.unmanned == {HOWO.car_code: 'moved'}
    routable = {s.customer_id: s for s in stops}
    amd = lambda code: dp._shortfall(ctx, routable, dp.build(ctx, stops, None, [code], 'now',   # noqa: E731
                                                             dp.SEAT_TRIAL_ITERATIONS))[2]
    assert 1 - amd(FORD.car_code) / amd(HOWO.car_code) > dp.SWAP_MIN_GAIN


def test_swap_threshold_and_logist_choice(monkeypatch):
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    monkeypatch.setattr(dp, 'SWAP_MIN_GAIN', 0.99)                 # выигрыш меньше порога — остаётся на своей
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now', crew)
    assert d.trucks == [HOWO.car_code] and d.seats == {} and d.unmanned == {FORD.car_code: 'absent'}
    monkeypatch.undo()
    manual = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Կարեն'}, manual={HOWO.car_code})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now', manual)
    assert d.trucks == [HOWO.car_code] and d.unmanned == {FORD.car_code: 'absent'}   # выбор логиста — не трогаем


def test_pinned_trip_of_absent_driver_stays():
    """Закреплённый рейс машины, чей водитель не вышел, остаётся (машина едет); свободный водитель — сначала на неё."""
    stops, _ = _dp_stops(EAST + WEST)
    ctx = _ctx((HOWO, FORD))
    old = dp.Draft(trucks=[HOWO.car_code, FORD.car_code], trips=[dp.DraftTrip(1, FORD.car_code, [101], pinned=True)],
                   next_id=2)
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, old, [HOWO.car_code, FORD.car_code], 'now', crew)
    assert FORD.car_code in d.trucks and d.unmanned == {} and d.seats == {}
    assert any(t.pinned and t.truck == FORD.car_code and t.stops == [101] for t in d.trips)
    view = dp.crew_view(d, crew)
    assert view[FORD.car_code] == {'name': 'Կարեն', 'seat': False, 'warn': 'absent', 'stale': False}
    free = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', 'OFF': 'Լևոն'}, absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, old, [HOWO.car_code, FORD.car_code], 'now', free)
    assert d.seats == {FORD.car_code: 'Լևոն'} and dp.crew_view(d, free)[FORD.car_code]['warn'] is None


# ============================== кто ведёт машину ==============================

def test_crew_view_logist_choice_twice_and_none():
    d = dp.Draft(trucks=['A', 'B', 'C'], seats={'A': 'Լևոն'})
    assert dp.crew_view(d, _crew({'A': 'Արամ', 'B': 'Կարեն'}, absent={'Արամ'})) == {
        'A': {'name': 'Լևոն', 'seat': True, 'warn': None, 'stale': False},
        'B': {'name': 'Կարեն', 'seat': False, 'warn': None, 'stale': False},
        'C': {'name': None, 'seat': False, 'warn': 'none', 'stale': False}}
    # после сборки логист поставил на A Գոռ (подмена дня) — главнее посадки сборки; Լևոն на B тоже — на двух машинах
    view = dp.crew_view(d, _crew({'A': 'Գոռ', 'B': 'Լևոն'}, absent={'Արամ'}, manual={'A', 'B'}))
    assert view['A'] == {'name': 'Գոռ', 'seat': False, 'warn': None, 'stale': False} and view['B']['warn'] is None
    d2 = dp.Draft(trucks=['A', 'B'], seats={'A': 'Լևոն'})
    assert dp.crew_view(d2, _crew({'A': 'Արամ', 'B': 'Լևոն'}, absent={'Արամ'}))['B']['warn'] == 'twice'
    # подмена логиста на A — тоже не вышла: посадка сборки остаётся
    assert dp.crew_view(d, _crew({'A': 'Գոռ'}, absent={'Գոռ'}, manual={'A'}))['A'] == \
        {'name': 'Լևոն', 'seat': True, 'warn': None, 'stale': False}


def test_crew_view_own_driver_changed_after_build():
    """Посадка сборки — пока свой водитель машины не вышел или ведёт другую машину дня. Постоянного водителя машины
    сменили после сборки (новый вышел и свободен) — карточка и Բեռնագիր с ним, план «пересобрать» (stale)."""
    d = dp.Draft(trucks=['A', 'B'], seats={'A': 'Լևոն'})
    assert dp.crew_view(d, _crew({'A': 'Գոռ', 'B': 'Կարեն'}))['A'] == {'name': 'Գոռ', 'seat': False, 'warn': None,
                                                                       'stale': True}
    # свой водитель A ведёт B (его поставили туда) — посадка нужна
    assert dp.crew_view(d, _crew({'A': 'Կարեն', 'B': 'Կարեն'}))['A'] == {'name': 'Լևոն', 'seat': True, 'warn': None,
                                                                          'stale': False}


def test_draft_json_roundtrip_and_old_drafts():
    d = dp.Draft(trucks=['A'], seats={'A': 'Լևոն'}, unmanned={'B': 'absent', 'C': 'moved'}, absent=['Արամ'])
    raw = d.to_json()
    assert (raw['seats'], raw['unmanned'], raw['absent']) == ({'A': 'Լևոն'}, {'B': 'absent', 'C': 'moved'}, ['Արամ'])
    back = dp.Draft.from_json(json.loads(json.dumps(raw)))
    assert (back.seats, back.unmanned, back.absent) == (d.seats, d.unmanned, d.absent)
    old = dp.Draft.from_json({'trucks': ['A']})
    assert (old.seats, old.unmanned, old.absent) == ({}, {}, []) and not {'seats', 'unmanned', 'absent'} & set(old.to_json())
    bad = dp.Draft.from_json({'seats': {'A': 5, 'B': ''}, 'unmanned': {'A': 'gone', 'B': 'moved'}, 'absent': 'x'})
    assert (bad.seats, bad.unmanned, bad.absent) == ({}, {'B': 'moved'}, [])


def test_spare_trucks_need_a_free_driver():
    """Совет «добавить машину» (№54) и новые заказы дня (№72) не предлагают машину, чей водитель не вышел или ведёт
    машину дня; машину без водителя в «Վարորդ» и со свободным вышедшим водителем — предлагают."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD, JAC, FORD2))
    crew = _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն', JAC.car_code: 'Արամ', FORD2.car_code: 'Լևոն'},
                 absent={'Կարեն'})
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code], 'now', crew)
    assert dp.spare_trucks(ctx, d, crew) == [FORD2.car_code]             # FORD — не вышел, JAC — Արամ на HOWO
    assert dp.spare_trucks(ctx, d, _crew({})) == sorted([FORD.car_code, JAC.car_code, FORD2.car_code])
    new, _ = _dp_stops(EAST + [(110, (40.21, 44.66), 50.0)])
    extras = {o['truck'] for o in dp.same_day_options(ctx, stops, new, d, [110], 0.0, crew)['options'] if o['kind'] == 'extra'}
    assert extras <= {FORD2.car_code}
    left = [{'customer_id': 101, 'kg': 10, 'no_room': True, 'no_center': False, 'no_vehicle': False}]
    assert dp._advice(ctx, d, [], left, crew)['add']['car_code'] == FORD2.car_code
    gone = _crew({**crew.own}, absent={'Կարեն', 'Լևոն'})
    advice = dp._advice(ctx, d, [], left, gone)
    assert advice['add'] is None and advice['no_free'] and advice['no_drivers']


def test_unmanned_not_offered_for_same_day_and_advice():
    """Машина без водителя сегодня — не «машина, которая не выезжала» для новых заказов дня (№72) и не совет «добавить»
    (№54): свободных водителей нет."""
    stops, _ = _dp_stops(EAST)
    ctx = _ctx((HOWO, FORD))
    d = dp.build_crewed(ctx, stops, None, [HOWO.car_code, FORD.car_code], 'now',
                        _crew({HOWO.car_code: 'Արամ', FORD.car_code: 'Կարեն'}, absent={'Արամ'}, manual={FORD.car_code}))
    new, _ = _dp_stops(EAST + [(110, (40.21, 44.66), 50.0)])
    opts = dp.same_day_options(ctx, stops, new, d, [110], 0.0)['options']
    assert opts and all(o['truck'] != HOWO.car_code for o in opts)
    advice = dp._advice(ctx, d, [], [{'customer_id': 101, 'kg': 10, 'no_room': True, 'no_center': False,
                                       'no_vehicle': False}])
    assert advice['add'] is None and advice['no_free'] and advice['no_drivers']


# ============================== отсутствие в базе ==============================

def test_store_absence_ranges(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    s.save_driver_absence('Արամ', '2026-10-05', None, 'qa')                 # только 05.10
    s.save_driver_absence('Կարեն', '2026-10-05', '2026-10-09', 'qa')        # отпуск по 09.10
    s.save_driver_absence('Կարեն', '2026-10-20', '2026-10-21', 'qa')        # и ещё раз потом
    assert s.driver_absences('2026-10-05') == {'Արամ': '2026-10-05', 'Կարեն': '2026-10-09'}
    assert s.driver_absences('2026-10-06') == {'Կարեն': '2026-10-09'} and s.driver_absences('2026-10-10') == {}
    s.save_driver_present('Կարեն', '2026-10-07', 'qa')                      # вернулся раньше: 05–06 остаются
    assert s.driver_absences('2026-10-06') == {'Կարեն': '2026-10-06'} and s.driver_absences('2026-10-07') == {}
    assert s.driver_absences('2026-10-20') == {'Կարեն': '2026-10-21'}       # будущий отпуск не тронут
    s.save_driver_present('Արամ', '2026-10-05', 'qa')                        # начатое в этот день — удаляется
    assert s.driver_absences('2026-10-05') == {'Կարեն': '2026-10-06'}
    s.save_driver_absence('Կարեն', '2026-10-20', '2026-10-25', 'qa')        # то же начало — срок исправлен
    assert s.driver_absences('2026-10-24') == {'Կարեն': '2026-10-25'}
    for bad in (('', '2026-10-05', None), ('Արամ', '2026-10-05', '2026-10-04'), ('Արամ', '05.10.2026', None),
                ('Ա​րամ', '2026-10-05', None)):
        with pytest.raises(ValueError):
            s.save_driver_absence(*bad, 'qa')


def test_store_migration_22_to_23_adds_absence_keeps_all_rows(tmp_path):
    """22 → 23: только CREATE TABLE driver_absence, прежние таблицы и строки как были."""
    step = next(v for v, ddl in st._MIGRATIONS.items() if st._DRIVER_ABSENCE_TABLE in ddl)
    assert step == 22 and st.SCHEMA_VERSION == 23
    path = str(tmp_path / 'v22.db')
    s = st.Store(path)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    s.save_dispatch('2026-10-01', {'trips': [{'id': 1, 'truck': 'CAR1', 'stops': [101]}]}, 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE driver_absence')
        conn.execute("UPDATE meta SET value = '22' WHERE key = 'schema_version'")
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    s = st.Store(path)
    assert s.driver_absences('2026-10-01') == {} and s.truck_drivers('2026-10-01')[0] == {'CAR1': 'Արամ'}
    with closing(sqlite3.connect(path)) as conn:
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before} == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ('23',)
    st.Store(path).save_driver_absence('Արամ', '2026-10-01', None, 'qa')     # повторное открытие — без миграции


# ============================== API ==============================

@pytest.fixture
def crew_day(client):
    """День 01.10: CAR1 (10 т, въезжает в центр) — Արամ, CAR2 (3,5 т) — Կարեն; магазины 101–104 — в малом центре."""
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    state.store.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    state.store.save_truck_driver('CAR2', '2026-10-01', 'Կարեն', 'qa')
    return state


def _absence(client, **body):
    return client.post('/api/routes/dispatch/absence', json={'date': '2026-10-01', **body})


def _build(client):
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_api_absence_rebuild_and_waybill(client, crew_day, monkeypatch):
    monkeypatch.setattr(views, '_clock', lambda: views.datetime(2026, 9, 30, 18, 0))
    page = client.get('/api/routes/dispatch?date=2026-10-01').get_json()
    assert page['crew'] == {'drivers': [{'name': 'Արամ', 'trucks': ['CAR1'], 'absent': False},
                                        {'name': 'Կարեն', 'trucks': ['CAR2'], 'absent': False}], 'trucks': {}, 'stale': False}
    built = _build(client)
    assert built['crew']['stale'] is False and \
        built['crew']['trucks']['CAR1'] == {'name': 'Արամ', 'seat': False, 'warn': None, 'stale': False}
    r = _absence(client, name='Արամ', absent=True)
    assert r.status_code == 200, r.get_json()
    crew = r.get_json()['crew']
    assert crew['drivers'][0] == {'name': 'Արամ', 'trucks': ['CAR1'], 'absent': True, 'until': '2026-10-01'}
    assert crew['stale'] is True                       # кто вышел — изменилось: пересобрать
    # магазины только в центре, куда въезжает лишь CAR1: Կարեն пересаживается на CAR1 (увозит всё), CAR2 — без водителя
    page = _build(client)
    assert page['crew']['stale'] is False
    assert page['crew']['trucks'] == {'CAR1': {'name': 'Կարեն', 'seat': True, 'warn': None, 'stale': False}}
    assert [(t['car_code'], t['selected'], t.get('unmanned')) for t in page['trucks']] == \
        [('CAR1', True, None), ('CAR2', True, 'moved')]
    assert [t['car_code'] for t in page['plan']['trucks']] == ['CAR1'] and page['plan']['unassigned'] == []
    client.application.extensions['route_optimizer'].waybill_loader = lambda isns: views.wb.Lines({}, frozenset(), {})
    w = client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': 'CAR1',
                                                                  'rev': page['rev']}).get_json()
    assert (w['driver'], w['driver_seat']) == ('Կարեն', True)
    # в данных для AI имён нет, причина машины без водителя — есть
    with client.application.test_request_context():
        state = client.application.extensions['route_optimizer']
        body = views._dispatch_body(views._load_day(state, views._bundle(state), views.date(2026, 10, 1)))
    assert 'Կարեն' not in str(body) and 'Արամ' not in str(body) and 'crew' not in body
    assert next(t for t in body['trucks'] if t['car_code'] == 'CAR2')['unmanned'] == 'moved'
    # вернулся — снова пересобрать; пересборка — как без отсутствий
    r = _absence(client, name='Արամ', absent=False)
    assert r.get_json()['crew']['drivers'][0]['absent'] is False and r.get_json()['crew']['stale'] is True
    page = _build(client)
    assert page['crew']['stale'] is False and [t.get('unmanned') for t in page['trucks']] == [None, None]
    assert page['crew']['trucks']['CAR1'] == {'name': 'Արամ', 'seat': False, 'warn': None, 'stale': False}


def test_api_absence_until_and_bad_requests(client, crew_day):
    r = _absence(client, name='Կարեն', absent=True, until='2026-10-03')
    assert r.status_code == 200 and r.get_json()['crew']['drivers'][1]['until'] == '2026-10-03'
    page = client.get('/api/routes/dispatch?date=2026-10-02').get_json()
    assert page['crew']['drivers'][1]['absent'] is True
    assert client.get('/api/routes/dispatch?date=2026-10-05').get_json()['crew']['drivers'][1]['absent'] is False
    for body, key in (({'name': 'Չկա', 'absent': True}, 'name'), ({'name': 'Արամ', 'absent': 'yes'}, 'absent'),
                      ({'name': 'Արամ', 'absent': True, 'until': '2026-09-30'}, 'until'),
                      ({'name': 'Արամ', 'absent': True, 'until': '2028-01-01'}, 'until'),
                      ({'name': 'Արամ', 'absent': False, 'until': '2026-10-03'}, 'until'),
                      ({'name': 5, 'absent': True}, 'name')):
        r = _absence(client, **body)
        assert r.status_code == 400 and key in r.get_json()['errors'], body


def test_api_logist_choice_after_build_wins(client, crew_day, monkeypatch):
    """Логист в «Վարորդ» поставил на машину другого водителя на этот день — Բեռնագիր и карточка сразу с ним."""
    monkeypatch.setattr(views, '_clock', lambda: views.datetime(2026, 9, 30, 18, 0))
    assert _absence(client, name='Արամ', absent=True).status_code == 200
    page = _build(client)
    assert page['crew']['trucks']['CAR1']['name'] == 'Կարեն'
    r = client.post('/api/routes/dispatch/driver', json={'date': '2026-10-01', 'car_code': 'CAR1', 'name': 'Գոռ',
                                                         'only_day': True})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['crew']['trucks']['CAR1'] == {'name': 'Գոռ', 'seat': False, 'warn': None, 'stale': False}


def test_api_stale_when_crew_changes_after_build(client, crew_day, monkeypatch):
    """После сборки: у машины без водителя снова есть свой (сменили в «Վարորդ») — пересобрать; у неотмеченной машины
    появился свободный водитель, а машины без водителя стоят — тоже; машины, удалённой из настроек, нет в счёте."""
    monkeypatch.setattr(views, '_clock', lambda: views.datetime(2026, 9, 30, 18, 0))
    assert _absence(client, name='Արամ', absent=True).status_code == 200
    page = _build(client)
    assert page['crew']['stale'] is False and [t.get('unmanned') for t in page['trucks']] == [None, 'moved']
    crew_day.store.save_truck_driver('CAR2', '2026-10-01', 'Գոռ', 'qa')      # у CAR2 новый постоянный водитель
    assert client.get('/api/routes/dispatch?date=2026-10-01').get_json()['crew']['stale'] is True
    crew_day.store.save_truck_driver('CAR2', '2026-10-01', 'Կարեն', 'qa')
    crew_day.store.save_truck_driver('CAR3', '2026-10-01', 'Լևոն', 'qa')      # в настройках CAR3 нет
    draft, _ = views._stored_draft(crew_day, views.date(2026, 10, 1))
    with client.application.test_request_context():
        day = views.date(2026, 10, 1)
        assert views._crew_json(crew_day, day, draft, {'CAR1', 'CAR2'})['crew']['stale'] is False
        got = views._crew_json(crew_day, day, draft, {'CAR1', 'CAR2', 'CAR3'})['crew']   # CAR3 есть: Լևոն свободен
        assert got['stale'] is True and [d['name'] for d in got['drivers']] == sorted(['Արամ', 'Կարեն', 'Լևոն'])


def test_api_waybill_blank_for_absent_driver_of_pinned_truck(client, crew_day, monkeypatch):
    """Закреплённый рейс машины, чей водитель не вышел, остаётся; в Բեռնագիր строка водителя пустая (вписать от руки)."""
    monkeypatch.setattr(views, '_clock', lambda: views.datetime(2026, 9, 30, 18, 0))
    page = _build(client)
    trip = page['plan']['trucks'][0]['trips'][0]
    truck = page['plan']['trucks'][0]['car_code']
    r = client.post('/api/routes/dispatch/edit', json={'date': '2026-10-01', 'rev': page['rev'], 'action': 'pin',
                                                       'trip': trip['id'], 'truck': truck})
    assert r.status_code == 200, r.get_json()
    name = {'CAR1': 'Արամ', 'CAR2': 'Կարեն'}[truck]
    assert _absence(client, name=name, absent=True).status_code == 200
    page = _build(client)
    assert page['crew']['trucks'][truck]['warn'] == 'absent' and not page['trucks'][0].get('unmanned')
    client.application.extensions['route_optimizer'].waybill_loader = lambda isns: views.wb.Lines({}, frozenset(), {})
    w = client.get('/api/routes/dispatch/waybill', query_string={'date': '2026-10-01', 'truck': truck,
                                                                  'rev': page['rev']}).get_json()
    assert w['driver'] is None and 'driver_seat' not in w

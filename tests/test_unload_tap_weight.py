# -*- coding: utf-8 -*-
"""Отметка водителя «закончил» и вес отгрузки во времени разгрузки (ответ владельца №65).

- разгрузка (learning.unload_obs) — до начала движения, но не дольше learning.TAP_TAIL после отметки доставки;
  факт визита (Visit.leave) не меняется;
- доставлено, кг — по весу товаров строк (courier.facts.delivered_share × вес накладной в learning.plan_stops);
  /day несёт weight_kg строки, снимок до №65 — прежняя доля штук.

Синтетические данные, ERP не читается, courier.db — временная. Запуск из корня проекта:
python -m pytest tests/test_unload_tap_weight.py -q
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from courier import day as dy  # noqa: E402
from courier import events as ev  # noqa: E402
from courier.erp_day import Line  # noqa: E402
from courier.facts import FactsSource, delivered_share  # noqa: E402
from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from test_courier_track import DAY, SHOP, _id, _pt, _track, _who, cs  # noqa: E402,F401
from test_learning_loop import DEPOT, A, Track, _stops, _t  # noqa: E402

D = date(2026, 9, 29)
ARRIVE = _t(10)


def _at(minutes):
    return ARRIVE + timedelta(minutes=minutes)


def _stop(key, cid, tap=None, window=None):
    return ac.PlanStop(key, cid, A, 500.0, 500.0, window, 0, _at(tap) if tap is not None else None)


def _minutes(stops, leave=30, keys=None):
    """Наблюдения разгрузки одного визита ARRIVE … ARRIVE + leave мин по точкам keys (по умолчанию — все)."""
    visit = ac.Visit(tuple(keys or [s.key for s in stops]), ARRIVE, _at(leave), 0, False)
    return [round(o.minutes, 6) for o in lr.unload_obs(D, ac.DayActual(0, 0.0, None, None, visits=(visit,)), stops)]


# ============================== A. конец разгрузки: отметка + 10 мин ==============================

def test_tap_caps_unload_at_tap_plus_tail():
    assert lr.TAP_TAIL == timedelta(minutes=10)
    assert _minutes([_stop('A', 101, tap=8)]) == [18.0]          # отметка на 8-й мин, уехал на 30-й → 18
    assert _minutes([_stop('A', 101, tap=25)]) == [30.0]         # 25 + 10 позже отъезда — до движения


def test_no_tap_or_tap_outside_stay_counts_until_motion():
    assert _minutes([_stop('A', 101)]) == [30.0]                  # отметки нет — до движения (№60)
    assert _minutes([_stop('A', 101, tap=35)]) == [30.0]          # после отъезда (в пределах DELIVERY_SLACK)
    assert _minutes([_stop('A', 101, tap=55)]) == [30.0]          # после отъезда и вне стоянки
    assert _minutes([_stop('A', 101, tap=-15)]) == [30.0]         # раньше прибытия больше чем на DELIVERY_SLACK


def test_tap_before_arrival_does_not_cap():
    # отметка до остановки у магазина — не «закончил» здесь (отметил заранее, например все магазины у предыдущего): до движения
    assert _minutes([_stop('A', 101, tap=-2)]) == [30.0]
    assert _minutes([_stop('A', 101, tap=-8)]) == [30.0]
    assert _minutes([_stop('A', 101, tap=0)]) == [10.0]            # в момент остановки — уже здесь: 0 + 10


def test_shared_stay_uses_latest_tap_within_stay():
    stops = [_stop('A', 101, tap=5), _stop('B', 102, tap=12)]
    assert _minutes(stops) == [22.0]                                # общая стоянка — до последнего «закончил»
    late = [_stop('A', 101, tap=5), _stop('B', 102, tap=33)]      # вторая — после отъезда: хвост не обрезается
    assert _minutes(late) == [30.0]
    far = [_stop('A', 101, tap=5), _stop('B', 102, tap=60)]       # вторая — вне стоянки: не в счёт
    assert _minutes(far) == [15.0]


def test_tail_first_then_window_wait_and_max():
    window = (10 * 60 + 10, float('inf'))                           # окно приёма с 10:10, приехал в 10:00
    assert _minutes([_stop('A', 101, tap=20, window=window)], leave=90) == [20.0]   # (30 − 0) − 10 ожидания
    assert _minutes([_stop('A', 101, tap=0, window=window)], leave=90) == []        # отметка за 10 мин до окна: 0
    # 2 ч у магазина (обед): без отметки — не разгрузка (> UNLOAD_MAX_MIN), с отметкой на 12-й мин — 22 мин магазина
    assert _minutes([_stop('A', 101)], leave=120) == []
    assert _minutes([_stop('A', 101, tap=12)], leave=120) == [22.0]


def test_cap_changes_only_the_unload_observation_not_the_visit():
    tr = Track(DEPOT, _t(8)).stay(10).drive(A, 30)
    arrive = tr.t
    tr.stay(40).drive(DEPOT, 30).stay(5)
    stops = _stops(A={'delivered_at': arrive + timedelta(minutes=8)})
    day = ac.reconstruct(tr.fixes, stops, DEPOT)
    visit = day.visits[0]
    assert visit.keys == ('A',) and visit.minutes == pytest.approx(40, abs=1.5)
    assert [o.customers for o in lr.unload_obs(D, day, stops)] == [(101,)]
    got = lr.unload_obs(D, day, stops)[0].minutes
    assert got == pytest.approx((arrive + timedelta(minutes=18) - visit.arrive).total_seconds() / 60.0)
    assert ac.stop_marks(day, stops, D)['A']['leave'] == visit.leave   # «план — факт»: настоящий отъезд


# ============================== B. доставлено по весу товаров ==============================

BOTTLE = {'line_id': 'x:1', 'product_id': 1, 'qty': 10.0, 'weight_kg': 195.0}   # 19 л: 10 × 19,5 кг
PACK = {'line_id': 'x:2', 'product_id': 2, 'qty': 24.0, 'weight_kg': 8.4}       # 0,33 л: 24 × 0,35 кг
STOP = {'stop_id': 'S:x', 'customer_id': 101, 'lat': A[0], 'lon': A[1], 'weight_kg': 203.4, 'seq': 1,
        'lines': [BOTTLE, PACK]}


def _delivery(*lines):
    return {'payload': {'lines': [{'line_id': lid, 'qty': q} for lid, q in lines]}}


def _kg(stop, delivery):
    """Доставлено, кг — как в обучении: доля delivered_share × вес накладной (learning.plan_stops)."""
    row = {**stop, 'delivered_share': delivered_share(stop, delivery), 'delivered_at': None}
    return lr.plan_stops([row], {}, {})[0].delivered_kg


def test_mixed_products_partial_delivery_counts_product_weight():
    # 5 бутылей из 10 и все 24 пачки: 5 × 19,5 + 8,4 = 105,9 кг (доля штук дала бы 29 / 34 × 203,4 ≈ 173,5)
    assert _kg(STOP, _delivery(('x:1', 5), ('x:2', 24))) == pytest.approx(105.9)
    assert _kg(STOP, _delivery(('x:1', 10), ('x:2', 0))) == pytest.approx(195.0)


def test_full_delivery_equals_invoice_weight():
    assert delivered_share(STOP, _delivery(('x:1', 10), ('x:2', 24))) == 1.0
    assert _kg(STOP, _delivery(('x:1', 10), ('x:2', 24))) == 203.4


def test_old_snapshot_without_line_weights_falls_back_to_qty_share():
    old = {**STOP, 'lines': [{k: v for k, v in ln.items() if k != 'weight_kg'} for ln in (BOTTLE, PACK)]}
    assert delivered_share(old, _delivery(('x:1', 5), ('x:2', 24))) == pytest.approx(29 / 34)
    assert _kg(old, _delivery(('x:1', 5), ('x:2', 24))) == pytest.approx(29 / 34 * 203.4)
    assert delivered_share(old, _delivery(('x:1', 30), ('x:2', 24))) == 1.0   # как прежде: прижато к 1 в целом


def test_unknown_line_ignored_and_over_delivery_clamped_per_line():
    assert _kg(STOP, _delivery(('x:1', 5), ('x:2', 24), ('zz:9', 100))) == pytest.approx(105.9)   # чужая строка
    assert _kg(STOP, _delivery(('x:1', 5), ('x:2', 40))) == pytest.approx(105.9)   # пачки сверх — не вместо бутылей
    assert _kg(STOP, _delivery(('x:1', 5))) == pytest.approx(97.5)                # строки пачек в доставке нет — 0
    assert delivered_share(STOP, None) is None


def test_line_without_weight_counts_zero_and_zero_weights_fall_back():
    unknown = {'line_id': 'x:3', 'product_id': 3, 'qty': 6.0, 'weight_kg': None}   # товара нет в ERP
    stop = {**STOP, 'lines': [BOTTLE, PACK, unknown]}
    assert _kg(stop, _delivery(('x:1', 5), ('x:2', 24), ('x:3', 0))) == pytest.approx(105.9)
    assert delivered_share(stop, _delivery(('x:1', 10), ('x:2', 24), ('x:3', 0))) == 1.0
    weightless = {**STOP, 'weight_kg': 0.0, 'lines': [{**BOTTLE, 'weight_kg': 0.0}, {**PACK, 'weight_kg': None}]}
    assert delivered_share(weightless, _delivery(('x:1', 5), ('x:2', 24))) == pytest.approx(29 / 34)   # доля штук


def test_day_lines_carry_weight_summing_to_stop_weight():
    data, _, _ = dy.demo_data()
    stops = dy.build_stops(data, [900001, 900002, 900003], {}, {})
    for s in stops:
        assert all(isinstance(ln['weight_kg'], float) for ln in s['lines'])
        assert sum(ln['weight_kg'] for ln in s['lines']) == pytest.approx(s['weight_kg'], abs=0.05)
    assert [ln['weight_kg'] for ln in stops[1]['lines']] == [8.4, 292.5]   # 24 × 0,35 и 15 × 19,5
    assert dy._line_json(Line('i', 1, 777, 3.0, 1.0, 3.0), None, (), None)['weight_kg'] is None   # товара нет


def test_facts_source_weighs_delivery_by_line_weights(cs):
    who = _who(cs)
    sid = 'S:%08d-3333-4333-8333-333333333333' % 1
    cs.save_day(DAY, 'CAR1', [{**STOP, 'stop_id': sid, 'customer': {'id': 101}, 'lat': SHOP[0], 'lon': SHOP[1]}],
                'v1', DAY + 'T08:00:00+04:00')
    deliv = {'id': _id(), 'type': 'delivery', 'stop_id': sid, 'date': DAY, 'at': DAY + 'T10:30:00+04:00',
             'payload': {'lines': [{'line_id': 'x:1', 'qty': 5}, {'line_id': 'x:2', 'qty': 24}], 'reason_id': 'closed'}}
    assert ev.ingest(cs, who, [_track([_pt(1), _pt(2)]), deliv]).json()['rejected'] == []
    got = FactsSource(cs).day('CAR1', DAY)['stops']
    assert got[0]['delivered_share'] == pytest.approx(105.9 / 203.4)
    assert lr.plan_stops(got, {}, {})[0].delivered_kg == pytest.approx(105.9)

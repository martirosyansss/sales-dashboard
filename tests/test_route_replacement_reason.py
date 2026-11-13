"""Окно замены машины называет магазин и правило, из-за которых машина недоступна."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import dispatch as dp, fleet as fl, replacement_api as ra, views
from route_optimizer.vehicle_access import VehicleAccess
from test_route_optimizer import DP_DAY, DP_DEPOT, DP_NORMS, TN, _dp_stops

JAC = fl.FleetTruck('JAC', 'JAC', 2200, 10, center_ok=True)
FORD = fl.FleetTruck('FORD', 'FORD', 2500, 14, center_ok=False)
ZONE = [(40.19, 44.51), (40.19, 44.53), (40.21, 44.53), (40.21, 44.51)]
INSIDE, OUTSIDE = (40.20, 44.52), (40.25, 44.60)


def day(stops, rules=None, zone=()):
    ctx = dp.DayContext(DP_DAY, DP_DEPOT, {t.car_code: t for t in (JAC, FORD)}, DP_NORMS, TN, 540,
                        overtime_minutes=660, vehicle_access=rules or {}, center_zone=zone)
    return SimpleNamespace(stops=stops, ctx=ctx)


@pytest.fixture(autouse=True)
def names(monkeypatch):
    monkeypatch.setattr(views, '_stop_info', lambda dd: lambda s: {'name': f'S{s.customer_id}', 'code': str(s.customer_id)})


def test_center_rule_names_store_and_allowed_truck():
    stops, _ = _dp_stops([(101, INSIDE, 100), (102, OUTSIDE, 100)])
    d = day(stops, {101: VehicleAccess('allow', ('JAC',))}, ZONE)
    assert ra._store_reason(d, 'FORD', {101, 102}) == '«S101» խանութը կենտրոնում է, թույլատրված է միայն JAC'


def test_center_without_rule_names_missing_permission():
    stops, _ = _dp_stops([(101, INSIDE, 100)])
    assert ra._store_reason(day(stops, {}, ZONE), 'FORD', {101}) == (
        '«S101» խանութը կենտրոնում է, իսկ մեքենան կենտրոն մուտք գործելու թույլտվություն չունի')


@pytest.mark.parametrize('rule, text', [
    (VehicleAccess('allow', ('JAC', 'X')), '«S101» խանութին թույլատրված է միայն JAC, X'),
    (VehicleAccess('allow', ()), '«S101» խանութին թույլատրված մեքենա չկա'),
    (VehicleAccess('deny', ('FORD',)), '«S101» խանութին այս մեքենան արգելված է'),
])
def test_store_rule_outside_center(rule, text):
    stops, _ = _dp_stops([(101, OUTSIDE, 100)])
    assert ra._store_reason(day(stops, {101: rule}), 'FORD', {101}) == text


def test_long_list_is_shortened():
    stops, _ = _dp_stops([(100 + i, OUTSIDE, 100) for i in range(5)])
    rules = {s.customer_id: VehicleAccess('deny', ('FORD',)) for s in stops}
    reason = ra._store_reason(day(stops, rules), 'FORD', {s.customer_id for s in stops})
    assert reason.count('խանութին') == 3 and reason.endswith('; և ևս 2 խանութ')


def test_allowed_truck_gets_empty_text_so_caller_keeps_original_error():
    stops, _ = _dp_stops([(101, INSIDE, 100)])
    assert ra._store_reason(day(stops, {101: VehicleAccess('allow', ('JAC',))}, ZONE), 'JAC', {101}) == ''


@pytest.mark.parametrize('rules, message', [
    ({101: VehicleAccess('allow', ('JAC',))}, 'не может обслуживать'),
    ({}, 'կենտրոն մուտք գործելու'),
])
def test_swap_reports_only_blocking_stores(rules, message):
    stops, _ = _dp_stops([(101, INSIDE, 100), (102, OUTSIDE, 100)])
    draft = dp.Draft(trucks=['JAC', 'FORD'], trips=[dp.DraftTrip(1, 'JAC', [101, 102])])
    with pytest.raises(dp.StoreTruckError, match=message) as caught:
        dp._swap_trucks(day(stops, rules, ZONE).ctx, stops, draft, {'truck': 'JAC', 'other_truck': 'FORD'}, True)
    assert caught.value.cids == (101,) and isinstance(caught.value, dp.DispatchError)

"""Складской остаток из неотправленного черновика не исчезает со сменой даты."""
import copy
from dataclasses import replace
from datetime import date, datetime

import pytest

from route_optimizer import dispatch as dp, views
from courier import routes_link as rl
from test_route_optimizer import client, _dorder, _isn  # noqa: F401
from test_route_dispatch_all_orders import (
    WED, THU, FRI, SAT, SIX, _thursday_sent, _state, _get, _row, _build, _edit, _stored, _now, _truck_of,
)


def _warehouse(client, monkeypatch, invoiced=True):
    _state(client).same_day_loader = None
    _state(client).driver_list_loader = lambda *args: []
    order = _dorder(1, 101, 400, day=WED)
    facts = _thursday_sent(client, monkeypatch, [order])
    if invoiced:
        facts.days[THU.isoformat()] = {'CAR1': {'closed_at': 'x', 'stops': [
            {'stop_id': 'S:' + _isn(20), 'replaces': ['O:' + order.isn],
             'customer_id': 101, 'status': 'pending'}]}}
        state = _state(client)
        load = state.dispatch_loader
        order = replace(order, shipped=THU, invoice=_isn(20))
        state.dispatch_loader = lambda *args: replace(load(*args), orders=(order,))
        state.dispatch_cache.clear()
    else:
        facts.stop(THU, 'CAR1', 1, 101)
    _build(client, FRI)
    assert client.post('/api/routes/settings', json={'settings': {'dispatch_backlog_days': 1}}).status_code == 200
    return order, facts


@pytest.mark.parametrize('target', [SAT, date(2026, 10, 5), date(2026, 10, 7)])
def test_unpublished_invoice_survives_midnight_and_missing_intermediate_plans(client, monkeypatch, target):
    order, _ = _warehouse(client, monkeypatch)
    state = _state(client)
    before = state.store.load_dispatch(FRI.isoformat())
    _now(monkeypatch, datetime.combine(target, datetime.min.time()).replace(hour=8))
    page = _get(client, target)
    row = _row(page['backlog'], 1)
    assert row is not None and row['taken'] and row['carried']
    assert page['orders']['kg'] == 400
    status = client.get('/api/routes/dispatch/status?date=' + target.isoformat()).get_json()
    assert status['orders']['kg'] == page['orders']['kg']
    assert state.store.load_dispatch(FRI.isoformat()) == before
    assert not rl.routes_view(state, target).released


def test_recovered_warehouse_is_sent_only_by_target_day_publication(client, monkeypatch):
    order, _ = _warehouse(client, monkeypatch)
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    monday = date(2026, 10, 5)
    page = _build(client, monday)
    truck = _truck_of(page, 101)
    assert not rl.pick_orders([order], monday, rl.routes_view(_state(client), monday), truck)
    code, page = _edit(client, page, monday, action='approve')
    assert code == 200
    assert rl.pick_orders([order], monday, rl.routes_view(_state(client), monday), truck) == [order]


def test_late_invoice_delivery_releases_warehouse_after_gap(client, monkeypatch):
    _, facts = _warehouse(client, monkeypatch)
    facts.days[THU.isoformat()]['CAR1']['stops'][0]['status'] = 'full'
    _state(client).closed_cache.clear()
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    assert _row(_get(client, date(2026, 10, 5))['backlog'], 1) is None


def test_order_without_invoice_survives_gap_too(client, monkeypatch):
    _, _ = _warehouse(client, monkeypatch, invoiced=False)
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    row = _row(_get(client, date(2026, 10, 5))['backlog'], 1)
    assert row and row['taken'] and row['carried']


def test_current_day_unsent_draft_does_not_seed_tomorrow_before_day_ends(client, monkeypatch):
    _warehouse(client, monkeypatch)
    assert views._day_auto(_state(client), SAT, _state(client).store.load().settings, None) == set()


def test_historical_day_keeps_its_saved_selection(client, monkeypatch):
    _warehouse(client, monkeypatch)
    _now(monkeypatch, datetime(2026, 10, 7, 8))
    assert views._day_auto(_state(client), SAT, _state(client).store.load().settings, None) == set()


def _known():
    return dp.Draft(auto_carried={_isn(1): (101, WED)})


def test_latest_warehouse_decision_and_publication_take_precedence():
    source = _known()
    target = date(2026, 10, 5)
    assert dp.warehouse_carried([(FRI, source)], target, SIX) == source.auto_carried
    for changes in ({'excluded': {_isn(1)}}, {'dismissed': {_isn(1)}}, {'dropped': {_isn(1)}}):
        decision = dp.Draft(**changes)
        assert not dp.warehouse_carried([(FRI, source), (SAT, decision)], target, SIX)
    future = dp.Draft(later={_isn(1): (date(2026, 10, 6), WED)}, excluded={_isn(1)})
    assert not dp.warehouse_carried([(FRI, source), (SAT, future)], target, SIX)
    assert dp.warehouse_carried([(FRI, source), (SAT, future)], date(2026, 10, 6), SIX) == source.auto_carried
    deferred = dp.Draft(deferred={_isn(1)}, excluded={_isn(1)})
    assert not dp.warehouse_carried([(FRI, source), (SAT, deferred)], date(2026, 10, 4), SIX)
    assert dp.warehouse_carried([(FRI, source), (SAT, deferred)], target, SIX) == source.auto_carried
    resumed = dp.Draft(added={_isn(1)})
    assert dp.warehouse_carried([(FRI, source), (SAT, dp.Draft(dismissed={_isn(1)})),
                                 (date(2026, 10, 4), resumed)], target, SIX) == source.auto_carried
    handed = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [101])], released={'at': 'x', 'by': None},
                       auto_carried=source.auto_carried)
    dp.send(handed, 'x', None, orders={_isn(1): ['CAR1', 400, 1000, 101]})
    assert not dp.warehouse_carried([(FRI, source), (SAT, handed)], target, SIX)


def test_unassigned_published_carry_remains_warehouse_and_inputs_are_unchanged():
    source = _known()
    source.released = {'at': 'x', 'by': None}
    dp.send(source, 'x', None, orders={})
    before = copy.deepcopy(source.to_json())
    assert dp.warehouse_carried([(FRI, source)], SAT, SIX) == source.auto_carried
    assert source.to_json() == before


def test_warehouse_respects_holidays_and_does_not_copy_unrelated_added_orders():
    source = _known()
    target = date(2026, 10, 5)
    deferred = dp.Draft(deferred={_isn(1)}, added={_isn(9)})
    plans = [(FRI, source), (SAT, deferred)]
    assert not dp.warehouse_carried(plans, target, SIX, {target})
    assert dp.warehouse_carried(plans, date(2026, 10, 6), SIX, {target}) == source.auto_carried
    assert not dp.warehouse_carried([(FRI, dp.Draft(added={_isn(9)}))], SAT, SIX)


def test_no_terminal_source_keeps_known_unpublished_warehouse(client, monkeypatch):
    _warehouse(client, monkeypatch)
    _state(client).live_facts = None
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    row = _row(_get(client, date(2026, 10, 5))['backlog'], 1)
    assert row and row['carried'] and row['taken']


def test_driver_and_warehouse_published_view_does_not_read_old_drafts(client, monkeypatch):
    _warehouse(client, monkeypatch)
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    state = _state(client)
    settings = state.store.load().settings
    assert _isn(1) in views._day_auto(state, date(2026, 10, 5), settings, None)
    assert not views._day_auto(state, date(2026, 10, 5), settings, None, live=False)

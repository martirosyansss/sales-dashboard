"""Additional transition-boundary checks, all in temporary SQLite databases."""
import sys
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from route_optimizer.store import Store
from route_optimizer import dispatch as dp
from test_route_optimizer import client, _isn  # noqa: F401
from test_warehouse_carry_days import _warehouse
from test_route_dispatch_all_orders import FRI, SAT, _state, _get, _row, _edit, _stored, _now, _truck_of


def _cargo():
    return dp.Draft(auto_carried={_isn(1): (101, date(2026, 9, 30))})


def _publish(draft, car='CAR1', at='2026-10-03T08:00:00'):
    draft.trips = [dp.DraftTrip(1, car, [101])]
    draft.released = {'at': at, 'by': 'a'}
    dp.send(draft, at, 'a', orders={_isn(1): [car, 1, 1, 101]})
    return draft


@pytest.mark.parametrize('decision', ['dismissed', 'future_defer'])
def test_computed_publication_cannot_override_newer_other_day_decision(tmp_path, decision):
    store = Store(str(tmp_path / 'r.db'))
    store.save_dispatch('2026-10-02', _cargo().to_json(), 'a')
    computed = _publish(_cargo())  # Prepared before the other-day dispatcher action.
    newer = _cargo()
    if decision == 'dismissed':
        newer.dismissed = {_isn(1)}
    else:
        newer.later = {_isn(1): (date(2026, 10, 10), date(2026, 9, 30))}
        newer.excluded = {_isn(1)}
    store.save_dispatch('2026-10-03', newer.to_json(), 'dispatcher')
    with pytest.raises(dp.DispatchError):
        store.save_dispatch('2026-10-05', computed.to_json(), 'stale other-day tab', expected_rev=0)
    assert store.load_dispatch('2026-10-05') is None


@pytest.mark.parametrize('decision', ['dismissed', 'future_defer'])
def test_explicit_resume_allows_publication_after_dispatcher_changes_decision(tmp_path, decision):
    store = Store(str(tmp_path / 'r.db'))
    draft = _cargo()
    if decision == 'dismissed':
        draft.dismissed = {_isn(1)}
    else:
        draft.later = {_isn(1): (date(2026, 10, 10), date(2026, 9, 30))}
        draft.excluded = {_isn(1)}
    rev = store.save_dispatch('2026-10-03', draft.to_json(), 'a')
    draft.dismissed.clear()
    draft.excluded.clear()
    draft.later.clear()
    draft.added.add(_isn(1))
    rev = store.save_dispatch('2026-10-03', draft.to_json(), 'a', expected_rev=rev,
                              warehouse_context={'action': 'include', 'selected': [_isn(1)]})
    _publish(draft)
    store.save_dispatch('2026-10-03', draft.to_json(), 'a', expected_rev=rev)
    assert store.warehouse_orders()[_isn(1)]['state'] == 'assigned'


def test_deferred_order_can_be_published_when_due_day_arrives(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    draft = _cargo()
    draft.later = {_isn(1): (date(2026, 10, 5), date(2026, 9, 30))}
    store.save_dispatch('2026-10-03', draft.to_json(), 'a')
    store.save_dispatch('2026-10-05', _publish(_cargo()).to_json(), 'a')
    assert store.warehouse_orders()[_isn(1)]['assigned_day'] == '2026-10-05'


def test_repeated_include_does_not_undo_same_day_published_handoff(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    handed = _publish(_cargo())
    handed.added = {_isn(1)}
    rev = store.save_dispatch('2026-10-03', handed.to_json(), 'a')
    row = store.warehouse_orders()[_isn(1)]
    store.save_dispatch('2026-10-03', handed.to_json(), 'a', expected_rev=rev,
                        warehouse_context={'action': 'include', 'selected': [_isn(1)]})
    assert store.warehouse_orders()[_isn(1)] == row


def test_withdrawal_does_not_invent_physical_return_without_terminal_evidence(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    handed = _publish(_cargo())
    rev = store.save_dispatch('2026-10-03', handed.to_json(), 'a')
    handed.trips = []
    dp.send(handed, '2026-10-03T08:01:00', 'a', orders={})
    store.save_dispatch('2026-10-03', handed.to_json(), 'a', expected_rev=rev)
    assert store.warehouse_orders()[_isn(1)]['state'] == 'assigned'


def test_store_removal_without_day_dismisses_published_carry_on_later_days(client, monkeypatch):
    """«Հանել առաքումից» в окне «×»: снятый перенос не едет и не становится нерешённым, вернуть можно из списка."""
    _warehouse(client, monkeypatch)
    page = _get(client, FRI)
    code, page = _edit(client, page, FRI, action='approve')
    assert code == 200, page
    code, page = _edit(client, page, FRI, action='defer_store', customer_id=101, to=None)
    assert code == 200, page
    assert _state(client).store.warehouse_orders()[_isn(1)]['state'] == 'dismissed'
    code, page = _edit(client, page, FRI, action='send')
    assert code == 200, page
    assert not _stored(client, FRI).sent['orders']
    _now(monkeypatch, datetime(2026, 10, 3, 8))
    row = _row(_get(client, SAT)['backlog'], 1)
    assert row is not None and row['dismissed'] and not row['taken']


@pytest.mark.parametrize('terminal', ['missing', 'pending', 'wrong_car', 'full', 'partial', 'refused'])
def test_removed_publication_needs_original_assignment_terminal_fact(client, monkeypatch, terminal):
    _, facts = _warehouse(client, monkeypatch)
    page = _get(client, FRI)
    car = _truck_of(page, 101)
    code, page = _edit(client, page, FRI, action='approve')
    assert code == 200, page
    trip = next(t for t in _stored(client, FRI).trips if 101 in t.stops)
    code, page = _edit(client, page, FRI, action='move', customer_id=101, from_trip=trip.id,
                       to_trip=None, truck=None)
    assert code == 200, page
    code, page = _edit(client, page, FRI, action='send')
    assert code == 200, page
    assert not _stored(client, FRI).sent['orders']
    row = _row(_get(client, FRI)['backlog'], 1)
    assert row is None or not row['taken']
    assert _state(client).store.warehouse_orders()[_isn(1)]['state'] == 'assigned'
    if terminal != 'missing':
        facts.days[FRI.isoformat()] = {('CAR2' if car == 'CAR1' else 'CAR1') if terminal == 'wrong_car' else car:
            {'closed_at': 'x', 'stops': [
            {'stop_id': 'S:' + _isn(20), 'replaces': ['O:' + _isn(1)],
             'customer_id': 101, 'status': 'pending' if terminal == 'wrong_car' else terminal}]}}
    _state(client).closed_cache.clear()
    _now(monkeypatch, datetime(2026, 10, 3, 8))
    row = _row(_get(client, SAT)['backlog'], 1)
    if terminal == 'pending':
        assert row and row['taken'] and row['carried']
    else:
        assert row is None or not row['taken']

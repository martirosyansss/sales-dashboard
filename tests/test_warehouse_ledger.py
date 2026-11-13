"""Состояние склада не зависит от возраста/удаления черновика и защищено от повторов и гонок."""
import json
import sqlite3
from datetime import date

import pytest

from route_optimizer.store import Store
from route_optimizer import dispatch as dp
from test_route_optimizer import _isn


def _known():
    return dp.Draft(auto_carried={_isn(1): (101, date(2026, 9, 30))})


def test_restart_pruning_and_repeated_snapshot_do_not_restore_handed_order(tmp_path):
    path = str(tmp_path / 'r.db')
    store = Store(path)
    draft = _known()
    store.save_dispatch('2026-10-02', draft.to_json(), 'a', expected_rev=0)
    handed = _known()
    handed.trips = [dp.DraftTrip(1, 'CAR1', [101])]
    handed.released = {'at': '2026-10-03T08:00:00', 'by': 'a'}
    dp.send(handed, '2026-10-03T08:00:00', 'a', orders={_isn(1): ['CAR1', 1, 1, 101]})
    store.save_dispatch('2026-10-03', handed.to_json(), 'a', expected_rev=0)
    before = store.warehouse_orders()[_isn(1)]
    store.save_dispatch('2026-10-05', draft.to_json(), 'a', expected_rev=0)
    restarted = Store(path)
    assert restarted.warehouse_orders()[_isn(1)] == before
    restarted.save_dispatch('2027-10-01', dp.Draft().to_json(), 'a', expected_rev=0)
    assert restarted.load_dispatch('2026-10-02') is not None   # retention counts from min(day, today): a far-future save prunes nothing
    restarted._transaction(lambda db: db.execute("DELETE FROM dispatch_plan WHERE day = '2026-10-02'"), 'test')   # the plan is pruned later
    assert restarted.load_dispatch('2026-10-02') is None
    assert restarted.warehouse_orders()[_isn(1)]['state'] == 'assigned'


def test_stale_terminal_proof_cannot_undo_new_handoff(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    draft = _known()
    store.save_dispatch('2026-10-02', draft.to_json(), 'a')
    draft.trips = [dp.DraftTrip(1, 'CAR1', [101])]
    draft.released = {'at': '2026-10-03T08:00:00', 'by': 'a'}
    dp.send(draft, '2026-10-03T08:00:00', 'a', orders={_isn(1): ['CAR1', 1, 1, 101]})
    store.save_dispatch('2026-10-03', draft.to_json(), 'a')
    row = store.warehouse_orders()[_isn(1)]
    proof = {_isn(1): {'day': row['assigned_day'], 'seq': row['seq']}}
    draft.trips[0].truck = 'CAR2'
    dp.send(draft, '2026-10-03T09:00:00', 'a', orders={_isn(1): ['CAR2', 1, 1, 101]})
    store.save_dispatch('2026-10-03', draft.to_json(), 'a')
    store.observe_warehouse({}, proof, [], '2026-10-04', 'observer')
    assert store.warehouse_orders()[_isn(1)]['state'] == 'assigned'
    fresh = store.warehouse_orders()[_isn(1)]
    store.observe_warehouse({}, {_isn(1): {'day': fresh['assigned_day'], 'seq': fresh['seq']}}, [], '2026-10-04', 'observer')
    assert store.warehouse_orders()[_isn(1)]['state'] == 'warehouse'


def test_plan_cas_and_ledger_transition_are_atomic(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    draft = _known()
    rev = store.save_dispatch('2026-10-02', draft.to_json(), 'a', expected_rev=0)
    draft.dismissed = {_isn(1)}
    assert store.save_dispatch('2026-10-02', draft.to_json(), 'b', expected_rev=0) is None
    assert store.warehouse_orders()[_isn(1)]['state'] == 'warehouse'
    rev = store.save_dispatch('2026-10-02', draft.to_json(), 'b', expected_rev=rev)
    assert store.warehouse_orders()[_isn(1)]['state'] == 'dismissed'
    store.save_dispatch('2026-10-05', _known().to_json(), 'old future tab', expected_rev=0)
    assert store.warehouse_orders()[_isn(1)]['state'] == 'dismissed'
    draft.dismissed.clear()
    draft.added = {_isn(1)}
    store.save_dispatch('2026-10-02', draft.to_json(), 'b', expected_rev=rev)
    assert store.warehouse_orders()[_isn(1)]['state'] == 'warehouse'


def test_old_schema_bootstrap_uses_publication_time_and_does_not_change_plans(tmp_path):
    path = str(tmp_path / 'r.db')
    store = Store(path)
    source, future = _known(), _known()
    store.save_dispatch('2026-10-02', source.to_json(), 'a')
    store.save_dispatch('2026-10-05', future.to_json(), 'a')
    sent = _known()
    sent.trips = [dp.DraftTrip(1, 'CAR1', [101])]
    sent.released = {'at': '2026-10-03T12:00:00+04:00', 'by': 'a'}
    dp.send(sent, '2026-10-03T12:00:00+04:00', 'a', orders={_isn(1): ['CAR1', 1, 1, 101]})
    store.save_dispatch('2026-10-03', sent.to_json(), 'a')
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE dispatch_plan SET updated_at='2026-10-02T06:00:00' WHERE day='2026-10-02'")
        conn.execute("UPDATE dispatch_plan SET updated_at='2026-10-03T04:00:00' WHERE day='2026-10-05'")
        conn.execute("UPDATE dispatch_plan SET updated_at='2026-10-03T08:00:00' WHERE day='2026-10-03'")
        before = conn.execute('SELECT * FROM dispatch_plan ORDER BY day').fetchall()
        conn.execute('DROP TABLE warehouse_event')
        conn.execute('DROP TABLE warehouse_order')
        conn.execute("DELETE FROM meta WHERE key='warehouse_initialized'")
        conn.execute("UPDATE meta SET value='29' WHERE key='schema_version'")
    migrated = Store(path)
    assert migrated.warehouse_orders()[_isn(1)]['state'] == 'assigned'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM dispatch_plan ORDER BY day').fetchall() == before


def test_closed_ledger_row_cannot_be_reopened_or_published_by_old_snapshot(tmp_path):
    store = Store(str(tmp_path / 'r.db'))
    draft = _known()
    store.save_dispatch('2026-10-02', draft.to_json(), 'a')
    store.observe_warehouse({}, {}, [_isn(1)], '2026-10-03', 'terminal closure')
    store.save_dispatch('2026-10-05', draft.to_json(), 'old tab')
    assert store.warehouse_orders()[_isn(1)]['state'] == 'closed'
    draft.trips = [dp.DraftTrip(1, 'CAR1', [101])]
    draft.released = {'at': 'x', 'by': None}
    dp.send(draft, 'x', None, orders={_isn(1): ['CAR1', 1, 1, 101]})
    with pytest.raises(dp.DispatchError):
        store.save_dispatch('2026-10-06', draft.to_json(), 'old tab')
    assert store.load_dispatch('2026-10-06') is None

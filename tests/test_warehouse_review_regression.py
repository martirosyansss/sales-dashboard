"""Adversarial review reproductions; no runtime or production changes."""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_route_optimizer import client  # noqa: F401
from test_warehouse_carry_days import _warehouse
from test_route_dispatch_all_orders import _now, _build, _edit, _get, _row, _state, SAT


def test_old_future_draft_must_not_restore_warehouse_after_driver_handoff(client, monkeypatch):
    order, facts = _warehouse(client, monkeypatch)
    _now(monkeypatch, datetime(2026, 10, 3, 8))
    monday, tuesday = date(2026, 10, 5), date(2026, 10, 6)
    _build(client, monday)  # Prepare a future draft while the goods still await assignment.
    saturday = _build(client, SAT)
    code, saturday = _edit(client, saturday, SAT, action='approve')
    assert code == 200
    assert order.isn in _state(client).store.load_dispatch(SAT.isoformat())[0]['sent']['orders']
    assert not facts.fleet(SAT.isoformat())  # No terminal evidence: location/outcome is unknown.
    _now(monkeypatch, datetime(2026, 10, 6, 8))
    row = _row(_get(client, tuesday)['backlog'], 1)
    assert row is None or not row['carried'], 'Stale Monday snapshot reactivated the already published Saturday cargo'


def test_old_future_draft_must_not_override_later_dispatcher_dismissal(client, monkeypatch):
    _warehouse(client, monkeypatch)
    _now(monkeypatch, datetime(2026, 10, 3, 8))
    monday, tuesday = date(2026, 10, 5), date(2026, 10, 6)
    _build(client, monday)
    saturday = _build(client, SAT)
    code, saturday = _edit(client, saturday, SAT, action='exclude', order='00000000-0000-0000-0000-000000000001')
    assert code == 200
    _now(monkeypatch, datetime(2026, 10, 6, 8))
    row = _row(_get(client, tuesday)['backlog'], 1)
    assert row is None or not row['carried'], 'Older future draft overrode a more recent dispatcher decision'


def test_known_warehouse_stock_does_not_disappear_after_ten_workdays(client, monkeypatch):
    _warehouse(client, monkeypatch)
    target = date(2026, 10, 20)
    _now(monkeypatch, datetime(2026, 10, 20, 8))
    row = _row(_get(client, target)['backlog'], 1)
    assert row is not None and row['carried'], 'Known warehouse goods disappeared at the ERP lookback boundary'

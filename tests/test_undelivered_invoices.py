"""Недовезённая накладная: следующий день в офисе и на терминале, без новой продажи ERP."""
from dataclasses import replace
from datetime import date

import pytest

from route_optimizer import dispatch as dp
from courier import erp_day as ed, routes_link as rl
from test_route_optimizer import client, _dorder, _isn  # noqa: F401
from test_route_dispatch_all_orders import (
    THU, FRI, WED, _thursday_sent, _state, _get, _row, _build, _edit, _truck_of,
)
from test_courier import fake_erp, ISN  # noqa: F401


def _invoice(status='pending', invoice=20, parent=1):
    return {'stop_id': 'S:' + _isn(invoice), 'replaces': ['O:' + _isn(parent)],
            'customer_id': 101, 'status': status}


@pytest.mark.parametrize('status,carried', [('pending', True), ('in_progress', True), ('full', False),
                                         ('partial', False), ('refused', False), ('covered', False)])
def test_invoice_delivery_status_controls_carry(status, carried):
    plan = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [101])], released={'at': 'x', 'by': None})
    fleet = {'CAR1': {'stops': [_invoice(status)]}}
    assert dp.auto_carried([(plan, fleet, True)]) == ({_isn(1)} if carried else set())
    assert dp.auto_carried([(plan, fleet, False)]) == set()
    plan.excluded.add(_isn(1))
    assert dp.auto_carried([(plan, fleet, True)]) == set()


def test_late_invoice_delivery_closes_order_carry_and_split_is_not_reissued():
    plan = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [101])], released={'at': 'x', 'by': None})
    pending = {'CAR1': {'stops': [_invoice()]}}
    late = {'CAR1': {'stops': [_invoice('full')]}}
    assert dp.closed_points(late) == {_isn(1)}
    assert dp.auto_carried([(plan, pending, True), (None, late, True)]) == set()
    split = {'CAR1': {'stops': [_invoice(), _invoice('full', invoice=21)]}}
    assert dp.auto_carried([(plan, split, True)]) == set()
    plan.auto_carried = {_isn(1): (101, WED)}
    assert dp.auto_carried([(plan, split, True)]) == set()


def test_invoice_on_terminal_survives_customer_removed_from_later_plan():
    plan = dp.Draft(trips=[dp.DraftTrip(1, 'CAR1', [102])], released={'at': 'x', 'by': None})
    fleet = {'CAR1': {'stops': [_invoice()]}}
    assert dp.auto_carried([(plan, fleet, True)]) == {_isn(1)}
    plan.excluded.add(_isn(1))
    assert dp.auto_carried([(plan, fleet, True)]) == set()


def test_shipped_order_requires_explicit_carry():
    order = replace(_dorder(1, 101, 40, day=WED), shipped=THU, invoice=_isn(20))
    assert dp.to_deliver([order], FRI, THU).backlog == []
    assert dp.to_deliver([order], FRI, THU, redelivery={order.isn}).backlog == [order]
    assert dp.to_deliver([replace(order, invoice='')], FRI, THU, redelivery={order.isn}).backlog == []


def test_office_to_driver_only_after_publication_and_late_delivery_removes_it(client, monkeypatch):
    order = _dorder(1, 101, 400, day=WED)
    facts = _thursday_sent(client, monkeypatch, [order])
    facts.days[THU.isoformat()] = {'CAR1': {'stops': [_invoice()], 'closed_at': 'x'}}
    state = _state(client)
    original = state.dispatch_loader
    invoiced = replace(order, shipped=THU, invoice=_isn(20))
    state.dispatch_loader = lambda *args: replace(original(*args), orders=(invoiced,))
    state.dispatch_cache.clear()
    page = _get(client, FRI)
    row = _row(page['backlog'], 1)
    assert row['carried'] and row['taken'] and page['orders']['kg'] == 400
    assert not rl.routes_view(state, FRI).released
    page = _build(client, FRI)
    truck = _truck_of(page, 101)
    assert not rl.pick_orders([invoiced], FRI, rl.routes_view(state, FRI), truck)
    code, page = _edit(client, page, FRI, action='approve')
    assert code == 200, page
    view = rl.routes_view(state, FRI)
    assert view.redelivery == frozenset({_isn(1)})
    assert rl.pick_orders([invoiced], FRI, view, truck) == [invoiced]
    assert not rl.pick_orders([invoiced], FRI, view, 'OTHER')
    assert not rl.pick_orders([invoiced], FRI, replace(view, redelivery=frozenset()), truck)
    facts.days[THU.isoformat()]['CAR1']['stops'][0]['status'] = 'full'
    state.closed_cache.clear()
    assert not _row(_get(client, FRI)['backlog'], 1)
    assert not rl.pick_orders([invoiced], FRI, rl.routes_view(state, FRI), truck)


def test_terminal_loads_existing_invoice_with_original_date_only_for_assigned_car(fake_erp, monkeypatch):
    today, yesterday = date(2026, 10, 2), date(2026, 10, 1)
    parent, invoice = ISN[2], ISN[0]
    fake_erp.orders = [(parent, 'Z0011', date(2026, 9, 30), 11, 7, 'OLD', 18000, 10,
                        yesterday, 0, None, invoice)]
    fake_erp.parents = [(invoice, parent)]
    original = ed._select
    queries = []

    def select(conn, sql, params=()):
        if sql.startswith(ed.SQL_REDELIVERY_SALES.split('{')[0]):
            queries.append(params)
            return [(invoice, '000318001', 11, 7, '1', 18000, yesterday)]
        return original(conn, sql, params)

    monkeypatch.setattr(ed, '_select', select)
    ed.clear_ref_cache()
    view = rl.RoutesView(plan_exists=True, released=True, trips=(('NEW', (11,)),),
                         published={parent: 'NEW'}, redelivery=frozenset({parent}))
    for car in ('NEW', 'OLD', 'OTHER'):
        data = ed.load_day('cs', car, today, (date(2026, 9, 29), today),
                           lambda orders, places: rl.pick_orders(orders, today, view, car, places),
                           rl.invoice_owner(view, car))
        if car == 'NEW':
            assert len(data.docs) == 1
            doc = data.docs[0]
            assert (doc.stop_id, doc.source, doc.amount, doc.doc_date, doc.replaces) == (
                'S:' + invoice, 'invoice', 18000, yesterday, ('O:' + parent,))
        else:
            assert data.docs == ()
    assert queries == [(today, invoice)]
    ed.clear_ref_cache()

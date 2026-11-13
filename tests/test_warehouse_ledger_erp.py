"""Старые известные документы читаются точечно и доходят до назначенного водителя без нового документа."""
from dataclasses import replace
from datetime import date, datetime

from route_optimizer import erp, dispatch as dp
from courier import erp_day as ed, routes_link as rl
from test_route_optimizer import client, _isn  # noqa: F401
from test_warehouse_carry_days import _warehouse
from test_route_dispatch_all_orders import _state, _now, _get, _row, _stored, FRI
from test_courier import fake_erp, ISN  # noqa: F401


def test_known_erp_lookup_is_parameterized_and_has_no_date_cutoff(monkeypatch):
    seen = []
    def select(conn, sql, params):
        erp.check_sql(sql)
        assert 'o.fISN IN (?)' in sql and 'o.fDATE >= ?' not in sql
        seen.append(params)
        return [(_isn(1), 'old', date(2024, 1, 1), 101, 1, '', 100, 20, None, 0, None, '')]
    monkeypatch.setattr(erp, '_select', select)
    rows = erp.dispatch_orders_by_isns(None, [_isn(1)])
    assert seen == [[_isn(1)]] or seen == [(_isn(1),)]
    assert rows[0].order_date == date(2024, 1, 1)


def test_office_fetches_only_missing_known_orders_after_long_gap(client, monkeypatch):
    order, _ = _warehouse(client, monkeypatch)
    state = _state(client)
    original = state.dispatch_loader
    template = original(None, None, None)
    state.dispatch_loader = lambda *args: replace(template, orders=())
    state.dispatch_cache.clear()
    calls = []
    def known(ids, day):
        calls.append((ids, day))
        return replace(template, orders=(order,))
    state.known_orders_loader = known
    target = date(2027, 1, 4)
    _now(monkeypatch, datetime(2027, 1, 4, 8))
    row = _row(_get(client, target)['backlog'], 1)
    assert row['taken'] and row['carried']
    assert calls == [([order.isn], target)]


def test_courier_fetches_old_order_only_for_published_owner(fake_erp, monkeypatch):
    today, original = date(2027, 1, 4), date(2026, 10, 1)
    parent, invoice = ISN[2], ISN[0]
    order = dp.DispatchOrder(parent, 'old', date(2026, 9, 30), 11, 7, 'OLD', 18000, 10, original,
                             invoice=invoice)
    calls = []
    def known(conn, ids):
        calls.append(ids)
        return [order]
    monkeypatch.setattr(erp, 'dispatch_orders_by_isns', known)
    monkeypatch.setattr(ed, 'morning_debts', lambda conn, ids, day: {})  # Общая фикстура долга привязана к 02.10.
    original_select = ed._select
    def select(conn, sql, params=()):
        if sql.startswith(ed.SQL_REDELIVERY_SALES.split('{')[0]):
            return [(invoice, 'same-number', 11, 7, '1', 18000, original)]
        return original_select(conn, sql, params)
    monkeypatch.setattr(ed, '_select', select)
    fake_erp.orders = []
    fake_erp.parents = [(invoice, parent)]
    view = rl.RoutesView(plan_exists=True, released=True, trips=(('NEW', (11,)),),
                         published={parent: 'NEW'}, redelivery=frozenset({parent}))
    ed.clear_ref_cache()
    for car in ('NEW', 'OTHER'):
        data = ed.load_day('cs', car, today, (date(2026, 12, 20), today),
                           rl.OrderPicker(today, view, car), rl.invoice_owner(view, car))
        assert [d.stop_id for d in data.docs] == (['S:' + invoice] if car == 'NEW' else [])
        if car == 'NEW':
            assert data.docs[0].doc_date == original and data.docs[0].doc_number == 'same-number'
    assert calls == [[parent]]
    ed.clear_ref_cache()


def test_persisted_defer_date_is_visible_on_other_day(client, monkeypatch):
    order, _ = _warehouse(client, monkeypatch)
    state = _state(client)
    draft = _stored(client, FRI)
    draft.later = {order.isn: (date(2026, 10, 6), order.order_date)}
    draft.excluded = {order.isn}
    state.store.save_dispatch(FRI.isoformat(), draft.to_json(), 'dispatcher')
    _now(monkeypatch, datetime(2026, 10, 5, 8))
    row = _row(_get(client, date(2026, 10, 5))['backlog'], 1)
    assert row['later_to'] == '2026-10-06' and not row['taken']

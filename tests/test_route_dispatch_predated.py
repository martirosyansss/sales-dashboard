# -*- coding: utf-8 -*-
"""«Развоз»: заказ, заведённый в ERP раньше своей даты (ответ владельца №79), — заказ и на эту дату: его везут уже в неё,
не отгрузили — на следующий рабочий день, если план его даты его не видел (видел — решён там: «с прошлых дней», без
повторной доставки); в «новые заказы дня» (№72) он не входит. Обычные заказы — как прежде. Синтетические данные, без ERP; база «Маршрутов» — временная (фикстура client).

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_predated.py -q
"""
import sys
from dataclasses import replace
from types import SimpleNamespace
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from courier import routes_link as rl  # noqa: E402
from route_optimizer import dispatch as dp  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_route_dispatch_same_day import D, NEXT, _build, _page_setup  # noqa: E402
from test_route_optimizer import _dorder, _isn, client  # noqa: E402,F401

SAT, SUN, MON = date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)
WEEK6 = (1, 2, 3, 4, 5, 6)


def _early(o, entered):
    return replace(o, entered=entered)


def test_predated_only_when_entered_before_its_date():
    o = _dorder(1, 101, 10.0, day=D)
    assert not o.predated                                                   # дня ввода нет — обычный
    assert not _early(o, D).predated and not _early(o, date(2026, 10, 2)).predated
    assert _early(o, date(2026, 9, 30)).predated


def test_window_of_day_adds_predated_orders_of_the_day():
    """Развоз пн 05.10 (рабочие пн–сб): окно — заказы субботы и воскресенья. Заказ, заведённый заранее на сам понедельник,
    — понедельника; на субботу — субботы и, не отгружен в субботу, понедельника; обычный заказ понедельника — развоз
    вторника; пятничный — «не отгружены с прошлых дней»."""
    since, _ = dp.order_window(MON, WEEK6)
    assert since == SAT
    before = date(2026, 10, 1)
    normal_sat, normal_sun = _dorder(1, 101, 10.0, day=SAT), _dorder(2, 102, 10.0, day=SUN)
    early_sat, early_sun = _early(_dorder(3, 103, 10.0, day=SAT), before), _early(_dorder(4, 104, 10.0, day=SUN), before)
    early_mon, normal_mon = _early(_dorder(5, 105, 10.0, day=MON), before), _dorder(6, 106, 10.0, day=MON)
    normal_fri = _dorder(7, 107, 10.0, day=date(2026, 10, 2))
    sel = dp.to_deliver([normal_sat, normal_sun, early_sat, early_sun, early_mon, normal_mon, normal_fri], MON, since)
    assert [o.customer_id for o in sel.main] == [101, 102, 103, 104, 105]
    assert [o.customer_id for o in sel.backlog] == [107]
    # отгружен в свою дату — в понедельник не везём
    shipped = replace(early_sat, shipped=SAT)
    assert [o.customer_id for o in dp.to_deliver([shipped], MON, since).main] == []
    # тот же заказ на субботу везут в субботу (окно субботы — пятница)
    sat_since, _ = dp.order_window(SAT, WEEK6)
    assert [o.customer_id for o in dp.to_deliver([early_sat, normal_fri], SAT, sat_since).main] == [103, 107]


def test_settle_predated_moves_orders_seen_by_previous_plan_to_backlog():
    """План субботы видел заказ, заведённый заранее на субботу (при сборке, в рейсе клиента, исключён, перенесён), — в
    понедельник он «с прошлых дней»; не видел (завели после сборки, клиент не в рейсах) — едет в понедельник."""
    since, _ = dp.order_window(MON, WEEK6)
    before = date(2026, 10, 1)
    built, in_trip, excl, defer, unseen = (_early(_dorder(i, 100 + i, 10.0, day=SAT), before) for i in range(1, 6))
    mon = _early(_dorder(6, 101, 10.0, day=MON), before)                  # заказ самого пн — не трогаем
    sel = dp.to_deliver([built, in_trip, excl, defer, unseen, mon], MON, since)
    draft = dp.Draft(trips=[dp.DraftTrip(1, 'C1', [102])], built_orders={built.isn: (10.0, 1.0)}, excluded={excl.isn},
                     deferred={defer.isn})
    out = dp.settle_predated(sel, since, dp.PlanSeen.of(draft))
    assert [o.customer_id for o in out.main] == [101, 105]
    assert [o.customer_id for o in out.backlog] == [101, 102, 103, 104]
    assert dp.settle_predated(sel, since, dp.PlanSeen.of(None)) == sel


def test_shipped_before_and_lists_count_predated_of_day():
    since, _ = dp.order_window(MON, WEEK6)
    early = _early(_dorder(1, 101, 10.0, day=MON, shipped=SAT), date(2026, 10, 1))      # отгружен заранее
    self_ = _early(_dorder(2, 102, 10.0, day=MON, agent=3, van=3), date(2026, 10, 1))   # везёт сам
    late = _dorder(3, 103, 10.0, day=MON)                                                # обычный — не этого дня
    sel = dp.to_deliver([early, self_, late], MON, since)
    assert sel.main == [] and sel.shipped_before == 1 and [o.customer_id for o in sel.self_delivery] == [102]


def test_same_day_candidates_skip_predated():
    orders = [_dorder(1, 101, 10.0, day=D), _early(_dorder(2, 102, 10.0, day=D), date(2026, 9, 29)),
              _early(_dorder(3, 103, 10.0, day=D), D)]
    assert [o.customer_id for o in dp.same_day_candidates(orders, D)] == [101, 103]


def test_erp_reads_entry_day(monkeypatch):
    erp.check_sql(erp.SQL_DISPATCH_ORDERS)
    assert 'fCREATIONDATE' in erp.SQL_DISPATCH_ORDERS and 'DOCUMENTS' in erp.SQL_DISPATCH_ORDERS
    rows = [(_isn(1), 'Z1', datetime(2026, 10, 5), 101, 7, ' C1 ', 1000, 10, None, 0, datetime(2026, 10, 2, 15, 4)),
            (_isn(2), 'Z2', date(2026, 10, 5), 102, 7, '', 500, 5, date(2026, 10, 5), 7, None)]
    monkeypatch.setattr(erp, '_select', lambda conn, sql, params=(): rows)
    a, b = erp.dispatch_orders(None, date(2026, 10, 1), date(2026, 10, 6))
    assert (a.order_date, a.entered, a.predated) == (MON, date(2026, 10, 2), True)
    assert (b.entered, b.predated, b.self_delivery) == (None, False, True)


def test_driver_app_gets_predated_order_on_its_date():
    view = rl.RoutesView(workdays=WEEK6, plan_exists=False)
    lo, hi = rl.orders_window(MON, view)
    assert hi == date(2026, 10, 6)                                   # ERP — и за сам день
    early = _early(_dorder(1, 101, 10.0, day=MON, car='C1'), date(2026, 10, 1))
    normal = _dorder(2, 102, 10.0, day=MON, car='C1')
    old = _dorder(3, 103, 10.0, day=SAT, car='C1')
    early_sat = _early(_dorder(4, 104, 10.0, day=SAT, car='C1'), date(2026, 10, 1))
    assert {o.customer_id for o in rl.pick_orders([early, normal, old, early_sat], MON, view, 'C1')} == {101, 103, 104}
    # план субботы его уже вёз (клиент в рейсе) — в понедельник водителю второй раз не даём
    seen = replace(view, seen=dp.PlanSeen(customers=frozenset({104})))
    assert {o.customer_id for o in rl.pick_orders([early, normal, old, early_sat], MON, seen, 'C1')} == {101, 103}


def test_page_plans_predated_order_on_its_date_not_as_new(client, monkeypatch):
    """Заказ магазина 101, заведённый 29.09 на 01.10, — в развозе 01.10 (в остановке 101 вместе с заказом 30.09), не в
    «новых заказах дня»; план 01.10 его вёз — в развоз 02.10 сам не идёт (накладной ещё нет — «с прошлых дней»)."""
    early = _early(_dorder(20, 101, 120.0, day=D), date(2026, 9, 29))
    _page_setup(client, monkeypatch, extra_orders=[early])
    d = _build(client)
    assert d['orders']['count'] == 4                                  # 3 заказа 30.09 + заведённый заранее
    stop = next(s for t in d['plan']['trucks'] for tr in t['trips'] for s in tr['stops'] if s['customer_id'] == 101)
    assert [o['isn'] for o in stop['orders']] == [_isn(1), _isn(20)]
    assert _isn(20) not in {o['isn'] for o in d['same_day']['orders']}
    assert d['same_day']['count'] == 3                                # новые — как без него
    nxt = client.get('/api/routes/dispatch?date=' + NEXT).get_json()
    assert nxt['orders']['count'] == 3                                # заказы 01.10: 103, 999, 104
    assert _isn(20) in {o['isn'] for o in nxt['backlog']}


def test_page_predated_order_without_plan_of_its_date_goes_next_day(client, monkeypatch):
    """Плана на 01.10 не собирали (или заказ завели после — его никто не видел): не отгружен — едет 02.10."""
    early = _early(_dorder(20, 101, 120.0, day=D), date(2026, 9, 29))
    _page_setup(client, monkeypatch, extra_orders=[early])
    nxt = client.get('/api/routes/dispatch?date=' + NEXT).get_json()
    assert nxt['orders']['count'] == 4 and _isn(20) not in {o['isn'] for o in nxt['backlog']}


def test_exclude_of_predated_taken_before_rule_is_plain_exclude():
    """Черновик до правила №79 взял заказ, заведённый заранее, как новый заказ дня; теперь он в заказах дня — его «не везём
    сегодня» — обычное, а взятый новый заказ дня — правка новых заказов."""
    early = _early(_dorder(20, 101, 10.0, day=D), date(2026, 9, 29))
    dd = SimpleNamespace(draft=dp.Draft(same_day={_isn(20), _isn(21)}), deliver=[early])
    assert not views._is_same_day_edit(dd, {'action': 'exclude', 'order': _isn(20).lower()})
    assert views._is_same_day_edit(dd, {'action': 'exclude', 'order': _isn(21)})

# -*- coding: utf-8 -*-
"""«Развоз»: правки по логическому ревью 12.10 (Codex, P1/P2). Каждый тест — сценарий ревью: до правки он падал.

1. ручной перенос магазина в другую машину переживает пересборку (Draft.locks);
2. магазин, разделённый между машинами, — каждый заказ ERP только одной машине (dp.order_trucks);
3. накладная одного заказа не прячет другой заказ того же клиента (courier.erp_day.pending_orders);
4. отправленный снимок фиксирует заказы (Draft.sent['orders']): заказ, пришедший после отправки, — не у водителя, а у
   логиста как «не отправлено»;
5. пересборка из устаревшей вкладки — 409 (rev);
6. разбиение отправленного рейса на два — «не отправлено»;
8. «сегодня» — одно, по Еревану;
9. ERP не ответила при обновлении — явный флаг erp_ok;
10. экипаж: перед утверждением и отправкой — проверка, после смены водителя отправленного плана — «не отправлено».
Синтетические данные, без ERP.

Запуск из корня проекта:  python -m pytest tests/test_route_dispatch_logic_fixes.py -q
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import dispatch as dp  # noqa: E402
from test_route_optimizer import EAST, WEST, _dorder, _dp_ctx, _dp_stops, _info, _isn  # noqa: E402

TRUCKS = ['991AT61', '333DO33']


def _where(draft, cid):
    return sorted({t.truck for t in draft.trips if cid in t.stops})


# ============================== 1. ручной перенос переживает пересборку ==============================

def test_manual_move_survives_rebuild_until_explicit_unlock():
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 104 in t.stops)
    natural, other = src.truck, next(c for c in TRUCKS if c != src.truck)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 104, 'from_trip': src.id,
                                              'to_trip': None, 'truck': other}, ids)
    dst = next(t for t in draft.trips if 104 in t.stops)
    assert _where(draft, 104) == [other] and draft.locks == {104: other}
    # пересборка — магазин остаётся у машины, куда его перенёс логист (до правки — возвращался в natural)
    rebuilt = dp.build(ctx, stops, draft, TRUCKS, 'now2')
    assert _where(rebuilt, 104) == [dst.truck] and rebuilt.locks == {104: dst.truck}
    view = dp.plan_view(ctx, stops, rebuilt, _info)
    marks = {s['customer_id']: s.get('locked') for t in view['trucks'] for tr in t['trips'] for s in tr['stops']}
    assert marks[104] is True and not any(v for c, v in marks.items() if c != 104)
    # переживает и сохранение (JSON)
    assert dp.Draft.from_json(json.loads(json.dumps(rebuilt.to_json()))).locks == {104: dst.truck}
    # снимает только явное действие — тогда пересборка снова решает сама
    copy = dp.Draft.from_json(rebuilt.to_json())
    cleared = dp.apply_edit(ctx, stops, rebuilt, {'action': 'unlock_all'}, ids)
    assert cleared.locks == {}
    assert _where(dp.build(ctx, stops, cleared, TRUCKS, 'now3'), 104) == [natural]
    one = dp.apply_edit(ctx, stops, copy, {'action': 'unlock', 'customer_id': 104}, ids)
    assert one.locks == {}


def test_lock_dropped_when_store_removed_or_truck_not_in_build():
    ctx = _dp_ctx()
    stops, orders = _dp_stops(EAST + WEST)
    ids = {o.isn for o in orders}
    draft = dp.build(ctx, stops, None, TRUCKS, 'now')
    src = next(t for t in draft.trips if 101 in t.stops)
    other = next(c for c in TRUCKS if c != src.truck)
    draft = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 101, 'from_trip': src.id,
                                              'to_trip': None, 'truck': other}, ids)
    assert draft.locks == {101: other}
    # пересборка без этой машины — решения нет (машину убрал сам логист)
    assert dp.build(ctx, stops, draft, [src.truck], 'now2').locks == {}
    # убран из рейсов — решения нет
    trip = next(t for t in draft.trips if 101 in t.stops)
    out = dp.apply_edit(ctx, stops, draft, {'action': 'move', 'customer_id': 101, 'from_trip': trip.id,
                                            'to_trip': None, 'truck': None}, ids)
    assert out.locks == {}
    # старый черновик без поля — прежний до байта
    assert 'locks' not in dp.Draft(['A'], trips=[dp.DraftTrip(1, 'A', [5])]).to_json()
    assert dp.Draft.from_json({'locks': {'7': 'A', 'x': 'B', '8': 3, '9': ''}}).locks == {7: 'A'}


# ============================== 6. границы рейсов отправленного плана ==============================

def test_split_of_sent_trip_into_two_trips_is_unsent():
    draft = dp.Draft(['A'], trips=[dp.DraftTrip(1, 'A', [101, 102])], next_id=2)
    dp.approve(draft, 'a', 'u')
    assert dp.unsent(draft) is None
    draft.trips = [dp.DraftTrip(1, 'A', [101]), dp.DraftTrip(2, 'A', [102])]
    assert dp.unsent(draft) == {'trucks': ['A'], 'orders': False}
    dp.send(draft, 'b', 'u')
    assert dp.unsent(draft) is None
    # и обратно: два рейса слили в один
    draft.trips = [dp.DraftTrip(1, 'A', [101, 102])]
    assert dp.unsent(draft) == {'trucks': ['A'], 'orders': False}

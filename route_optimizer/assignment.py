"""Единые ограничения ручных назначений и отдельный факт передачи загруженного рейса."""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence

from . import dispatch as dp, fleet as fl


def weights_of(draft: dp.Draft, routable: Mapping[int, dp.Stop]) -> dict[tuple[int, int], float]:
    # trip_kgs отдаёт только исключения для магазинов нескольких машин; остальные — равные доли рейсов.
    special, _ = dp.trip_kgs(draft, routable)
    shares = dp._shares(draft.trips)
    return {(t.id, c): special.get((t.id, c), routable[c].kg / shares[c])
            for t in draft.trips for c in t.stops if c in routable}


def validate(ctx: dp.DayContext, stops: Sequence[dp.Stop], draft: dp.Draft,
             before: dp.Draft | None = None, *, reserve: bool = False, confirm_reserve: bool = False,
             require_available: bool = True, check_time: bool = True,
             before_stops: Sequence[dp.Stop] | None = None) -> None:
    routable = {s.customer_id: s for s in stops if s.point is not None}
    old_routable = ({s.customer_id: s for s in before_stops if s.point is not None}
                    if before_stops is not None else routable)
    weights = weights_of(draft, routable)
    old_weights = weights_of(before, old_routable) if before is not None else {}
    old = {t.id: t for t in before.trips} if before is not None else {}
    changed = [t for t in draft.trips if before is None or t.id not in old
               or (t.truck, t.stops, t.not_before, t.origin) != (old[t.id].truck, old[t.id].stops, old[t.id].not_before, old[t.id].origin)
               or any(abs(weights.get((t.id, c), 0) - old_weights.get((t.id, c), 0)) > .01 for c in t.stops)]
    if not changed:
        return
    selected = dp._selected(ctx, draft.trucks)
    reserve_messages = []
    for trip in changed:
        truck = ctx.trucks.get(trip.truck)
        if truck is None or (require_available and (trip.truck not in draft.trucks or trip.truck in draft.unmanned)):
            raise dp.DispatchError(f'{trip.truck}՝ մեքենան պատրաստ չէ․ ընտրեք աշխատող մեքենա')
        previous = old.get(trip.id)
        same_truck = previous is not None and previous.truck == trip.truck
        new_stops = [c for c in trip.stops if not same_truck or c not in previous.stops]
        dp._require_vehicle(ctx, new_stops, trip.truck)
        if any(dp._central(ctx, routable[c]) and not truck.center_ok for c in new_stops):
            raise dp.DispatchError(f'{trip.truck}՝ կենտրոն մուտք գործելու թույլտվություն չկա')
        kgs = [weights.get((trip.id, c), 0) for c in trip.stops]
        kg = math.fsum(kgs)
        old_kg = math.fsum(old_weights.get((trip.id, c), 0) for c in previous.stops) if same_truck else 0
        # решение владельца 09.11.2026: сверх тоннажа — до overload_bound. Правка (before) не создаёт перегруз, если день машины
        # выходит за предел; утверждение и отправка (before нет) судят рейс сам по себе, как over_capacity: день мог вырасти потом
        bound = dp.overload_bound(truck.capacity_kg)
        day_kg = math.fsum(weights.get((t.id, c), 0) for t in draft.trips if t.truck == trip.truck for c in t.stops)
        if not (kg <= bound and (before is None or day_kg <= bound)) and kg > max(truck.capacity_kg, old_kg) + .01:
            raise dp.DispatchError(f'{trip.truck}՝ երթ №{trip.id}, բեռը {kg:.1f} կգ է, '
                                   f'տարողությունը՝ {truck.capacity_kg:.1f} կգ։ Ընտրեք ավելի տարողունակ մեքենա։')
        limit = fl.load_limit(kgs, [dp._central(ctx, routable[c]) for c in trip.stops],
                              [dp._allowed_trucks(ctx, c) for c in trip.stops], truck, selected, ctx.tn.load_cap)
        if reserve and kg > max(limit, old_kg) + .5:
            reserve_messages.append(f'{trip.truck}՝ երթ №{trip.id}, {kg:.1f} / {truck.capacity_kg:.1f} կգ, '
                                    f'պլանավորման սահմանը՝ {limit:.1f} կգ։')
    if before is not None and check_time:
        times = dp._timeline(ctx, draft.trips, routable, dp._shares(draft.trips), kg_of=weights)
        old_times = dp._timeline(ctx, before.trips, old_routable, dp._shares(before.trips), kg_of=old_weights)
        old_arrivals = {}
        for previous in before.trips:
            for c, at in zip(previous.stops, old_times.get(previous.id, (0, 0, []))[2]):
                old_arrivals[c] = max(old_arrivals.get(c, -math.inf), at)
        end = ctx.overtime_minutes if draft.overtime_ok and ctx.overtime_minutes is not None else ctx.tn.work_minutes
        for trip in changed:
            start, minutes, arrivals = times[trip.id]
            previous = old_times.get(trip.id)
            previous_end = sum(previous[:2]) if previous is not None else end
            if start + minutes > max(end, previous_end) + .01:
                raise dp.DispatchError(f'{trip.truck}՝ մեքենան չի հասցնում վերադառնալ աշխատանքային օրվա ավարտին')
            if any(at > max(dp._real_end(ctx, c), old_arrivals.get(c, -math.inf)) + .01 for c, at in zip(trip.stops, arrivals)):
                raise dp.DispatchError(f'{trip.truck}՝ մեքենան չի հասցնում խանութի ընդունման ժամին')
    if reserve_messages and not confirm_reserve:
        raise dp.ReserveEdit('\n'.join(reserve_messages) + '\nՕգտագործե՞լ պլանավորման պահուստը։')


def basis(draft: dp.Draft, stops: Sequence[dp.Stop], trip: dp.DraftTrip) -> dict[str, Any]:
    routable = {s.customer_id: s for s in stops}
    weights = weights_of(draft, routable)
    owner, _ = dp.assign(draft, [o for s in stops for o in s.orders])
    return {'truck': trip.truck, 'stops': list(trip.stops),
            'kg': [[c, round(weights.get((trip.id, c), 0), 3)] for c in trip.stops],
            'orders': sorted([[o.isn, round(o.kg, 3)] for s in stops if s.customer_id in trip.stops
                              for o in s.orders if owner.get(o.isn) == trip.truck])}


def pending(draft: dp.Draft) -> list[dict[str, Any]]:
    return [r for r in draft.handovers if r['status'] == 'pending']


def require_ready(draft: dp.Draft) -> None:
    if pending(draft):
        raise dp.DispatchError('Նախ հաստատեք բեռի փաստացի փոխանցումը նոր մեքենային')


def register(before: dp.Draft, draft: dp.Draft, stops: Sequence[dp.Stop]) -> None:
    """Прежняя погрузка остаётся историей; новая машина ещё не объявляется загруженной."""
    old = {t.id: t for t in before.trips}
    for trip in draft.trips:
        previous = old.get(trip.id)
        if previous is None or previous.loaded is None or previous.truck == trip.truck:
            continue
        if len(draft.handovers) >= 100:
            raise dp.DispatchError('Այս օրվա բեռի փոխանցումների սահմանը լրացել է')
        cargo = basis(draft, stops, trip)
        draft.handovers.append({'id': f'{trip.id}:{len(draft.handovers) + 1}:{previous.truck}:{trip.truck}',
                                'trip': trip.id, 'from': previous.truck, 'to': trip.truck,
                                'old_loaded': dict(previous.loaded), 'basis': cargo, 'status': 'pending'})
        trip.loaded = None
        trip.pinned = True


def guard_pending(before: dp.Draft, draft: dp.Draft, stops: Sequence[dp.Stop]) -> None:
    for record in pending(before):
        trip = next((t for t in draft.trips if t.id == record['trip']), None)
        if trip is None or basis(draft, stops, trip) != record['basis']:
            raise dp.DispatchError('Սպասվում է բեռի փոխանցման հաստատումը․ նախ ավարտեք կամ չեղարկեք այն')


def confirm(draft: dp.Draft, stops: Sequence[dp.Stop], raw_id: Any, at: str, user: str | None) -> dp.Draft:
    out = copy.deepcopy(draft)
    record = next((r for r in out.handovers if r['id'] == raw_id and r['status'] == 'pending'), None)
    if record is None:
        raise dp.DispatchError('Փոխանցումը չի գտնվել կամ արդեն հաստատված է․ թարմացրեք էջը')
    trip = next((t for t in out.trips if t.id == record['trip']), None)
    if trip is None or basis(out, stops, trip) != record['basis']:
        raise dp.DispatchError('Բեռը կամ երթը փոխվել է․ նորից ստուգեք փոխանցումը')
    record.update(status='confirmed', at=at, by=user)
    trip.loaded = {'at': at, 'by': user, 'pin': False}
    trip.pinned = True
    return out


def guard_rollback(before: dp.Draft, restored: dp.Draft) -> None:
    kept = {r['id'] for r in restored.handovers}
    if any(r['status'] == 'confirmed' and r['id'] not in kept for r in before.handovers):
        raise dp.DispatchError('Բեռի փաստացի փոխանցումն արդեն հաստատված է․ այն սևագրի չեղարկմամբ չի վերադարձվում')

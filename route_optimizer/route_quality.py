"""Доводка состава рейсов по реальной стоимости, с проверкой всего дня машины.

Решатель использует приближённую стоимость рёбер. Здесь проверяются переносы,
обмены и объединения рейсов с учётом остаточного груза, разгрузки, погрузки,
обеда и почасового движения. Закреплённые и отдельные рейсы остаются на месте.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import heapq
import itertools
import math
from typing import Collection, Mapping, Sequence

from . import fleet as fl


MAX_PASSES = 6
MAX_CANDIDATES = 64
MAX_PROPOSALS = 20000


def improve_routes(trips: list[fl.Trip], stops: Sequence[fl._Stop], distances: fl.Matrix,
                   minutes: fl.Matrix, trucks: Sequence[fl.FleetTruck], tn: fl.TruckNorms,
                   used: Mapping[str, float] | None,
                   fixed: Sequence[tuple[str, Sequence[int], float]] | None,
                   fed: Collection[str] = ()) -> list[fl.Trip]:
    if len(trips) < 2:
        return trips
    by_code = {truck.car_code: truck for truck in trucks}
    shares = Counter(i for trip in trips for i in trip.items)
    visits = fl._shared(trips, stops, tn)
    start = {code: float((used or {}).get(code, 0.0)) for code in by_code}
    ends = {}
    baseline = fl._days(trips, visits, minutes, start, tn, fed, ends)
    locked = {code for code, rows in baseline.items()
              if rows is None or fl._late(rows, tn, ends.get(code, 0.0))}
    fixed_keys = {(code, tuple(items)) for code, items, _ in (fixed or ())}
    departures = {(trips[k].truck, trips[k].items): depart
                  for rows in baseline.values() if rows for k, depart, _, _ in rows}
    waiting = {code: math.fsum(wait for _, _, _, wait in rows)
               for code, rows in baseline.items() if rows}
    original_vehicle = {i: trip.truck for trip in trips for i in trip.items if shares[i] == 1}
    out = list(trips)
    passes = MAX_PASSES if len(stops) <= 80 else 2

    def eligible(trip):
        return (not trip.extra and trip.truck not in locked
                and (trip.truck, trip.items) not in fixed_keys
                and all(shares[i] == 1 and not visits[i].solo for i in trip.items))

    def permitted(code, items):
        truck = by_code[code]
        if not items:
            return True
        if math.fsum(visits[i].kg for i in items) > fl._load_limit(
                items, visits, truck, trucks, tn.load_cap) + fl._EPS:
            return False
        return all((truck.center_ok or not visits[i].center)
                   and fl._vehicle_allowed(visits[i], truck)
                   and not (visits[i].yerevan and truck.big
                            and not by_code[original_vehicle[i]].big) for i in items)

    def scheduled(rows, affected):
        tail = {}
        days = fl._days(rows, visits, minutes, start, tn, fed, tail)
        for code in affected:
            day = days.get(code, [])
            if day is None or fl._late(day, tn, tail.get(code, 0.0)):
                return None
            if math.fsum(wait for _, _, _, wait in day) > waiting.get(code, 0.0) + fl.WAIT_MERGE_SLACK + fl._EPS:
                return None
            for k, depart, _, _ in day:
                key = rows[k].truck, rows[k].items
                if key in fixed_keys and depart > departures[key] + fl._EPS:
                    return None
        return days

    for _ in range(passes):
        free = [k for k, trip in enumerate(out) if eligible(trip)]
        if len(free) < 2:
            break
        costs = {}

        def value(code, items):
            key = code, tuple(items)
            if key not in costs:
                truck = by_code[code]
                cost = fl._sequence_cost(items, visits, distances, truck).total_amd(tn.fuel_price)
                bias = fl._yerevan_bias(items, visits, truck, tn)
                costs[key] = cost + bias
            return costs[key]

        def insert(items, item, code):
            return min(([*items[:p], item, *items[p:]] for p in range(len(items) + 1)),
                       key=lambda seq: (value(code, seq), tuple(seq)))

        def proposal(a, b, left, right):
            ta, tb = out[a], out[b]
            if not permitted(ta.truck, left) or not permitted(tb.truck, right):
                return None
            delta = (value(ta.truck, left) + value(tb.truck, right)
                     - value(ta.truck, ta.items) - value(tb.truck, tb.items))
            if delta >= -1e-6:
                return None
            # Порядок детерминирован; нет секундного таймера, меняющего ответ.
            return (delta, a, b, tuple(left), tuple(right))

        def candidates():
            examined = 0
            for a in free:
                for b in free:
                    if a == b:
                        continue
                    ta, tb = out[a], out[b]
                    for item in ta.items:
                        left = [i for i in ta.items if i != item]
                        # Все позиции: самая дешёвая вставка может нарушать окно,
                        # тогда проверяется следующая допустимая позиция.
                        for p in range(len(tb.items) + 1):
                            examined += 1
                            if examined > MAX_PROPOSALS:
                                return
                            right = [*tb.items[:p], item, *tb.items[p:]]
                            row = proposal(a, b, left, right)
                            if row is not None:
                                yield row
                    if a < b:
                        for item in ta.items:
                            for other in tb.items:
                                examined += 1
                                if examined > MAX_PROPOSALS:
                                    return
                                left = insert([i for i in ta.items if i != item], other, ta.truck)
                                right = insert([i for i in tb.items if i != other], item, tb.truck)
                                row = proposal(a, b, left, right)
                                if row is not None:
                                    yield row
                    # Объединение целого рейса позволяет убрать погрузку, даже
                    # когда отдельный промежуточный перенос ещё невыгоден.
                    if len(ta.items) > 1:
                        for seq in (ta.items, ta.items[::-1]):
                            for p in range(len(tb.items) + 1):
                                examined += 1
                                if examined > MAX_PROPOSALS:
                                    return
                                right = [*tb.items[:p], *seq, *tb.items[p:]]
                                row = proposal(a, b, [], right)
                                if row is not None:
                                    yield row

        def materialize(changes):
            rows = []
            for k, trip in enumerate(out):
                if k not in changes:
                    rows.append(trip)
                    continue
                seq = tuple(changes[k])
                if not seq:
                    continue
                cost = fl._sequence_cost(seq, visits, distances, by_code[trip.truck])
                rows.append(replace(trip, items=seq, stops=len(seq),
                                    kg=math.fsum(visits[i].kg for i in seq),
                                    revenue=math.fsum(visits[i].revenue for i in seq),
                                    km=fl._closed(seq, visits, distances), liters=cost.liters,
                                    wear_amd=cost.wear_amd, payload_tonne_km=cost.payload_tonne_km))
            return rows

        improved = False
        for _, a, b, left, right in heapq.nsmallest(MAX_CANDIDATES, candidates()):
            affected = {out[a].truck, out[b].truck}
            changes = {a: left, b: right}
            trial = materialize(changes)
            if scheduled(trial, affected) is None:
                continue
            # Доводка порядка — с теми же окнами и последующими рейсами.
            for k in (a, b):
                if len(changes[k]) < 2:
                    continue
                def allowed(seq, k=k):
                    return scheduled(materialize({**changes, k: seq}), affected) is not None
                changes[k] = fl._cost_order(list(changes[k]), visits, distances,
                                            by_code[out[k].truck], tn, allowed)
            trial = materialize(changes)
            days = scheduled(trial, affected)
            if days is None:
                continue
            durations = {k: duration for code in affected for k, _, duration, _ in days.get(code, [])}
            out = [replace(trip, minutes=durations[k]) if k in durations else trip
                   for k, trip in enumerate(trial)]
            improved = True
            break
        if not improved:
            break
    # Первый рейс бывает загружен с вечера. При том же пробеге тяжёлый рейс
    # первым экономит дневную погрузку; окна могут требовать обратный порядок.
    # Рассматривается полная шкала, включая обед и следующие закрепления.
    for code in sorted({trip.truck for trip in out} - locked):
        slots = [k for k, trip in enumerate(out) if trip.truck == code and eligible(trip)]
        if len(slots) < 2:
            continue
        baseline_days = scheduled(out, {code})
        if baseline_days is None:
            continue
        day = baseline_days.get(code, [])
        best_key = (day[-1][1] + day[-1][2], math.fsum(row[2] for row in day))
        best = out
        orders = (itertools.permutations(slots) if len(slots) <= 5 else
                  [sorted(slots, key=lambda k: (-out[k].kg, k)), list(reversed(slots))])
        for order in orders:
            trial = list(out)
            for slot, old in zip(slots, order):
                trial[slot] = out[old]
            days = scheduled(trial, {code})
            if days is None:
                continue
            day = days.get(code, [])
            key = (day[-1][1] + day[-1][2], math.fsum(row[2] for row in day))
            if (key[0] < best_key[0] - fl._EPS or
                    abs(key[0] - best_key[0]) <= fl._EPS and key[1] < best_key[1] - fl._EPS):
                best, best_key = trial, key
        if best is not out:
            days = scheduled(best, {code})
            durations = {k: duration for k, _, duration, _ in days[code]}
            out = [replace(trip, minutes=durations[k]) if k in durations else trip
                   for k, trip in enumerate(best)]
    return out

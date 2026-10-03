"""Часовые скорости GPS с проверкой на последней неделе; текущих пробок здесь нет."""
from __future__ import annotations
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from .geo import haversine_km, in_city, usable_fixes


@dataclass(frozen=True)
class TrafficProfile:
    factors: dict[tuple[bool, int, int], float] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)

    def factor(self, city, weekday, minute):
        return self.factors.get((city, int(weekday >= 5), int(minute // 60) % 24), 1.)

    def minimum(self, city):
        return min([1., *(f for (c, _, h), f in self.factors.items() if c == city and 8 <= h <= 20)])

    def travel(self, km, speed, city, weekday, minute):
        left, elapsed = km, 0.
        while left > 1e-10:
            clock = minute + elapsed
            wd = (weekday + int(clock // 1440)) % 7
            velocity = speed * self.factor(city, wd, clock)
            span = 60 - clock % 60
            distance = velocity * span / 60
            if left <= distance:
                return elapsed + left / velocity * 60
            left -= distance; elapsed += span
        return elapsed


def learn(visits, fixes_by_agent, center, radius, today):
    blocked, bounds, locations = defaultdict(list), {}, defaultdict(list)
    for v in visits:
        key = v.agent_id, v.day
        blocked[key].append((v.start - timedelta(minutes=2),
                            (v.end or v.start + timedelta(minutes=10)) + timedelta(minutes=2)))
        lo, hi = bounds.get(key, (v.start, v.end or v.start))
        bounds[key] = min(lo, v.start), max(hi, v.end or v.start)
        if v.lat is not None and v.lon is not None:
            locations[key].append((v.lat, v.lon))
    rows = []
    for agent, fixes in fixes_by_agent.items():
        points = usable_fixes(fixes, max_accuracy=50)
        segments = []
        for a, b in zip(points, points[1:]):
            minutes = (b.at - a.at).total_seconds() / 60
            km = haversine_km(a.point, b.point)
            key = agent, a.at.date()
            envelope = bounds.get(key)
            valid = (a.at.date() == b.at.date() and 0 < minutes <= 3 and km / minutes * 60 <= 120
                     and envelope is not None and 7 <= a.at.hour <= 20
                     and envelope[0] - timedelta(hours=1) <= a.at <= b.at <= envelope[1] + timedelta(hours=1)
                     and not any(a.at < end and b.at > start for start, end in blocked[key])
                     and not any(haversine_km(a.point, p) < .1 or haversine_km(b.point, p) < .1 for p in locations[key]))
            move = valid and km >= .05 and km / minutes * 60 >= 6
            segments.append((a, b, km, minutes, valid, move))
        for i, (a, b, km, minutes, valid, moving) in enumerate(segments):
            bridge = (valid and minutes <= 2 and i > 0 and i+1 < len(segments)
                      and segments[i-1][5] and segments[i+1][5])
            if not valid or not (moving or bridge):
                continue
            city = in_city(a.point, center, radius) and in_city(b.point, center, radius)
            cursor = a.at
            while cursor < b.at:
                boundary = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                end = min(b.at, boundary)
                span = (end - cursor).total_seconds() / 60
                rows.append((cursor.date(), city, int(cursor.weekday() >= 5), cursor.hour, km * span / minutes, span))
                cursor = end
    cut = today - timedelta(days=7)
    totals, buckets, days, held = (defaultdict(lambda: [0., 0.]), defaultdict(lambda: [0., 0.]),
                                   defaultdict(set), defaultdict(lambda: [0., 0.]))
    for day, city, group, hour, km, minutes in rows:
        if day < cut:
            totals[city][0] += km; totals[city][1] += minutes
            key = city, group, hour
            buckets[key][0] += km; buckets[key][1] += minutes; days[key].add(day)
        elif cut <= day < today:
            held[(day, city, group, hour)][0] += km; held[(day, city, group, hour)][1] += minutes
    candidate = {}
    for key, (km, minutes) in buckets.items():
        city = key[0]
        if len(days[key]) >= 3 and minutes >= 45 and totals[city][1] >= 180 and km > 0:
            candidate[key] = max(.35, min(1.75, (km / minutes) / (totals[city][0] / totals[city][1])))
    factors, validation = {}, []
    for city in (True, False):
        base_km, base_min = totals[city]
        errors = []
        for (day, c, group, hour), (km, minutes) in held.items():
            key = c, group, hour
            if c == city and key in candidate and minutes >= 5 and base_km > 0:
                estimate = km * base_min / base_km
                errors.append((abs(estimate-minutes), abs(estimate/candidate[key]-minutes), day))
        before, after = math.fsum(e[0] for e in errors), math.fsum(e[1] for e in errors)
        accepted = len(errors) >= 6 and len({e[2] for e in errors}) >= 3 and after < before * .98
        if accepted:
            factors.update({k: f for k, f in candidate.items() if k[0] == city})
        validation.append({'area': 'city' if city else 'region', 'bins': len(errors), 'accepted': accepted,
                           'baseline_mae_min': round(before/len(errors), 2) if errors else None,
                           'profile_mae_min': round(after/len(errors), 2) if errors else None})
    return TrafficProfile(factors, {'source': 'historical_gps', 'live': False,
        'status': 'validated' if factors else 'insufficient_or_rejected', 'train_end': cut.isoformat(),
        'test_end': today.isoformat(), 'segments': len(rows), 'hours_supported': len(factors),
        'validation': validation, 'scope': 'Исторические скорости GPS менеджеров; текущие заторы неизвестны'})


class TravelMatrix(list):
    def __init__(self, values, distances, cities, norms, tn, points):
        super().__init__(values)
        self.distances, self.cities, self.norms, self.tn = distances, cities, norms, tn
        self.points = points

    def load(self, kg):
        return self.tn.load(kg) if self.tn is not None else 0.

    def travel(self, a, b, minute):
        if self.norms.provider is not None:
            return self.norms.provider.minutes(self.points[a], self.points[b])
        city = self.cities[a] and self.cities[b]
        speed = self.norms.speed_city_kmh if city else self.norms.speed_region_kmh
        if self.norms.traffic is None:
            return self.distances[a][b] / speed * 60
        return self.norms.traffic.travel(self.distances[a][b], speed, city,
            self.norms.traffic_weekday, self.norms.traffic_start_min + minute)

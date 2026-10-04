# -*- coding: utf-8 -*-
"""Синтетические GPS-дни с известной правдой для обучения «как у профессионалов» (№66: запас на рейс).

Парк машин; у одной — медленный экипаж (разгрузка × SLOW_UNLOAD, путь × SLOW_TRAVEL). Каждый день каждая машина делает
два рейса по случайным магазинам; правда относительно модели «Развоза» (по прямой × 1,3 / 25 км/ч, разгрузка 8 + 6 мин/т):
минуты участка = модель × темп машины × шум машино-дня × шум участка, разгрузка — так же со своим шумом; шум машино-дня
общий для всех участков и визитов этого дня (связь ошибок внутри дня). Трек — точки раз в 15 с в пути и раз в 60 с на
стоянке, факт восстанавливает actuals.reconstruct, наблюдения — learning.trip_obs.
Без БД и ERP; используется tests/test_learning_pro.py.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from route_optimizer import actuals as ac
from route_optimizer import evaluate as evm
from route_optimizer import fleet as fl
from route_optimizer import learning as lr
from route_optimizer import store as rst
from route_optimizer.geo import Fix

TZ = ac.YEREVAN
DEPOT = (40.19462, 44.6004)
CENTER = (40.1792, 44.4991)
CARS = ('CAR1', 'CAR2', 'CAR3', 'CAR4', 'CAR5', 'CAR6')
SLOW = 'CAR4'
SLOW_UNLOAD = 1.30
SLOW_TRAVEL = 1.25
DAY_SD = 0.10            # шум машино-дня (лог), общий для его участков и визитов
LEG_SD = 0.12            # шум участка (лог)
VISIT_SD = 0.20          # шум визита (лог)
WORK_START = 9 * 60


def settings() -> dict:
    return {**rst.DEFAULT_SETTINGS, 'truck_lunch_min': 0}


def norms() -> evm.Norms:
    return evm.Norms.from_settings(settings())


def truck_norms() -> fl.TruckNorms:
    return fl.TruckNorms.from_settings(settings(), lunch=True)


def stores(n: int = 60, seed: int = 7) -> dict[int, tuple[float, float]]:
    rnd = random.Random(seed)
    out = {}
    for cid in range(1000, 1000 + n):
        r, a = 7.0 * math.sqrt(rnd.random()), rnd.uniform(0, 2 * math.pi)
        out[cid] = (CENTER[0] + r * math.cos(a) / 111.2, CENTER[1] + r * math.sin(a) / (111.2 * math.cos(math.radians(40.18))))
    return out


class Track:
    def __init__(self, start, at):
        self.pos, self.t = start, at
        self.fixes = [Fix(at, *start, 8.0)]

    def drive(self, to, minutes, step=15):
        secs = minutes * 60.0
        n = max(1, int(secs // step))
        a = self.pos
        for i in range(1, n + 1):
            k = i / n
            self.fixes.append(Fix(self.t + timedelta(seconds=secs * k), a[0] + (to[0] - a[0]) * k,
                                  a[1] + (to[1] - a[1]) * k, 8.0))
        self.t += timedelta(seconds=secs)
        self.pos = to

    def stay(self, minutes, step=60):
        n = int(minutes * 60 // step)
        for i in range(1, n + 1):
            self.fixes.append(Fix(self.t + timedelta(seconds=i * step), *self.pos, 8.0))
        self.t += timedelta(seconds=minutes * 60)


@dataclass(frozen=True)
class CarDay:
    car: str
    day: date
    stops: tuple[ac.PlanStop, ...]
    actual: ac.DayActual


def model_minutes(nm: evm.Norms, a, b) -> float:
    return nm.km(a, b) / nm.speed_city_kmh * 60.0


def simulate(days: list[date], seed: int = 1, cars=CARS, per_trip: int = 5) -> list[CarDay]:
    """Машино-дни: два рейса по per_trip магазинов, правда — в шапке модуля."""
    rnd = random.Random(seed)
    nm, tn = norms(), truck_norms()
    pts = stores()
    out = []
    for d in days:
        for car in cars:
            tu, tt = (SLOW_UNLOAD, SLOW_TRAVEL) if car == SLOW else (1.0, 1.0)
            day_eff = math.exp(rnd.gauss(0.0, DAY_SD))
            cids = rnd.sample(sorted(pts), 2 * per_trip)
            kg = {c: rnd.choice((150.0, 300.0, 600.0, 1000.0)) for c in cids}
            tr = Track(DEPOT, datetime(d.year, d.month, d.day, 8, 45, tzinfo=TZ))
            tr.stay(15)
            for trip in (cids[:per_trip], cids[per_trip:]):
                for c in trip:
                    tr.drive(pts[c], model_minutes(nm, tr.pos, pts[c]) * tt * day_eff * math.exp(rnd.gauss(0.0, LEG_SD)))
                    tr.stay(tn.unload(kg[c]) * tu * day_eff * math.exp(rnd.gauss(0.0, VISIT_SD)))
                tr.drive(DEPOT, model_minutes(nm, tr.pos, DEPOT) * tt * day_eff * math.exp(rnd.gauss(0.0, LEG_SD)))
                tr.stay(20)
            stops = tuple(ac.PlanStop(f'S:{c}', c, pts[c], kg[c], kg[c], None, i) for i, c in enumerate(cids, 1))
            out.append(CarDay(car, d, stops, ac.reconstruct(tr.fixes, list(stops), DEPOT)))
    return out


def observations(sim: list[CarDay], tn: fl.TruckNorms | None = None):
    """Рейсы — наблюдения запаса по факту, прогноз — нормами tn (нет — из настроек)."""
    nm, tn = norms(), tn or truck_norms()
    trips = []
    for cd in sim:
        trips += lr.trip_obs(cd.day, cd.car, cd.actual, cd.stops, nm, tn, DEPOT, WORK_START)
    return trips

def coverage(trips, c: float) -> float:
    return sum(1 for o in trips if o.minutes <= o.predicted + fl.trip_reserve(c, o.predicted) + 1e-9) / len(trips)

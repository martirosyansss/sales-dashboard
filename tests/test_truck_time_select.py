# -*- coding: utf-8 -*-
"""Выбор модели времени в пути грузовиков по трекам водителей (learning-loop-plan.md, этап 4; вид обучения truck_time).

- честное сравнение: у каждой модели — своя поправка по часам на днях обучения, обе проверяются на одних и тех же
  участках отложенной недели тем профилем, который применится; участок без минут Valhalla не идёт ни в одну модель;
- правило выбора: другая модель — только при ошибке меньше хотя бы на 2% и достаточных данных (пороги — на границе),
  в обе стороны (гистерезис, без «туда-сюда»);
- порядок источников: переменная окружения ROUTES_TRUCK_TIME > выбор обучения (автообучение вида включено) > прежняя
  модель; срез грузовика с другим выбором минут — тот же срез матриц, без общего изменяемого состояния;
- применение в «Развозе»: минуты грузовиков и road_model_id из одного среза; поправка по часам — только своей модели
  (минуты Valhalla от скоростей зон не зависят); прежняя модель — те же рейсы до бита;
- ночной прогон: переключение вместе с поправкой новой модели, повтор дня — тот же итог, env не даёт переключить;
- журнал (схема 14: вид truck_time) и страница «Обучение и факт».

Только синтетика: треки водителей (настоящих ещё нет), подделка движка Valhalla (FakeActor), временные базы; ERP не
читается.  Запуск из корня проекта:  python -m pytest tests/test_truck_time_select.py -q
"""
import random
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip('numpy')

from route_optimizer import actuals as ac  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import roads as rd  # noqa: E402
from route_optimizer import store as rst  # noqa: E402
from route_optimizer import valhalla_engine as ve  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.geo import haversine_km, in_city  # noqa: E402
from route_optimizer.traffic_validation import TrafficProfile  # noqa: E402
from test_learning_loop import TZ, Track  # noqa: E402
from test_route_optimizer import DP_DEPOT, _dispatch_setup, _dorder, _no_road_map, client  # noqa: E402,F401
from test_route_valhalla import (NEW, NORMS, P, FakeActor, FakeOsm, _metric, _preparers, _provider,  # noqa: E402,F401
                                 _registry, _wait, fake)

TODAY = date(2026, 10, 3)
TEST_FROM = TODAY - timedelta(days=lr.HOLDOUT_DAYS)          # 26.09: проверка 26.09–02.10
MODEL, VALHALLA = lr.TRUCK_TIME_SOURCES
TF = f'|tf{ve.TIME_FACTOR[True]:g}/{ve.TIME_FACTOR[False]:g}'


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Переключатели — по умолчанию (ROUTES_TRUCK_TIME не задана), тайлов на диске нет."""
    monkeypatch.delenv('ROUTES_TRUCK_TIME', raising=False)
    monkeypatch.delenv('ROUTES_ROAD_ENGINE', raising=False)
    monkeypatch.setenv('ROUTES_VALHALLA_DIR', str(tmp_path / 'no-valhalla'))


@pytest.fixture
def bases(fake, tmp_path):
    """Нормы грузовиков обеих моделей из одного среза (подделка движка, матрица грузовика для точек P)."""
    reg, cost = _registry(tmp_path / 'v'), ve.truck_costing(5000)
    reg.matrix(ve.PROFILE_TRUCK, cost).ensure(P)
    view = ve.ValhallaRoads(reg, ve.PROFILE_CAR, FakeOsm(missing=()), time_only=True, truck_cost=cost)
    norms = replace(NORMS, roads=view)
    return {m: norms.for_trucks(truck_time=m == VALHALLA) for m in lr.TRUCK_TIME_SOURCES}


# ============================== синтетические пары участков ==============================

def _pair(day, hour, actual, km, model, valhalla):
    """Пара наблюдений одного участка (выезд в начале часа, без часового профиля): (прежняя модель, Valhalla)."""
    wd = day.weekday()
    return tuple(lr.LegObs(day, True, wd >= 5, hour, actual, m, m, km, km / m * 60.0, wd, hour * 60.0)
                 for m in (model, valhalla))


def _pairs(make, days=40, per_day=10, seed=5):
    """Участки за days дней до TODAY; make(rnd, km) → (факт, прежняя модель, Valhalla), минуты."""
    rnd = random.Random(seed)
    out = []
    for i in range(days):
        d = TODAY - timedelta(days=days - i)
        for j in range(per_day):
            km = 2.0 + 7.0 * rnd.random()
            actual, model, valhalla = make(rnd, km)
            out.append(_pair(d, 9 + j % 5, actual, km, model, valhalla))
    return out


def valhalla_knows(rnd, km):
    """Valhalla знает скорость участка (15–40 км/ч), факт — 1,3 × Valhalla; прежняя модель — км / 25 км/ч."""
    v = km / (15.0 + 25.0 * rnd.random()) * 60
    return 1.3 * v, km / 25.0 * 60, v


def model_knows(rnd, km):
    """Наоборот: факт — 1,2 × прежняя модель, у Valhalla — скорость участка «с потолка»."""
    m = km / 25.0 * 60
    return 1.2 * m, m, km / (15.0 + 25.0 * rnd.random()) * 60


def scale_off(rnd, km):
    """Valhalla — вдвое быстрее факта, но форма точная; прежняя модель — без смещения, но ±15% на участке."""
    y = km / (15.0 + 25.0 * rnd.random()) * 60
    return y, y * (1 + 0.15 * (2 * rnd.random() - 1)), y / 2


def _duel(better, gain, per_day=10):
    """Обучение: обе модели точны (множитель 1). Проверка: у better ошибка (1 − gain) мин на участок, у другой — 1 мин
    (знак чередуется: смещения нет)."""
    out = []
    for i in range(40):
        d = TODAY - timedelta(days=40 - i)
        for j in range(per_day):
            sign = 1 if j % 2 else -1
            err = {MODEL: 1.0, VALHALLA: 1.0, better: 1.0 - gain} if d >= TEST_FROM else {MODEL: 0.0, VALHALLA: 0.0}
            out.append(_pair(d, 9 + j % 4, 10.0, 4.0, 10.0 + sign * err[MODEL], 10.0 + sign * err[VALHALLA]))
    return out


def _counted(train_legs, train_days, test_legs, test_days):
    """Ровно столько участков и дней: Valhalla точна, у прежней модели — ±2 мин."""
    out = []
    for n, days, first in ((train_legs, train_days, TEST_FROM - timedelta(days=train_days)),
                           (test_legs, test_days, TEST_FROM)):
        for k in range(n):
            out.append(_pair(first + timedelta(days=k % days), 9 + k % 3, 10.0, 4.0, 10.0 + (2.0 if k % 2 else -2.0),
                             10.0))
    return out


# ============================== честное сравнение и правило выбора ==============================

def test_accepts_valhalla_when_truly_better_scored_with_applied_profile(bases):
    pairs = _pairs(valhalla_knows)
    o, fitted = lr.fit_truck_time(pairs, TODAY, MODEL, bases, {})
    c = o.params['candidates']
    assert o.kind == 'truck_time' and o.accepted and o.params['source'] == VALHALLA, o.reason
    assert c[VALHALLA]['learned']['mae'] < 0.05 < c[MODEL]['learned']['mae']
    assert (o.mae_before, o.mae_after) == (c[MODEL]['learned']['mae'], c[VALHALLA]['learned']['mae'])
    assert o.reason.startswith('Valhalla-ն ավելի ճշգրիտ է, քան նախկին մոդելը․')
    test = [p for p in pairs if p[0].day >= TEST_FROM]
    assert (o.n_test, o.params['legs']['test'], o.params['days']['test']) == (len(test), len(test), lr.HOLDOUT_DAYS)
    assert (o.test_from, o.test_to) == ('2026-09-26', '2026-10-02')
    # ошибка — ровно той поправки, которая применится (принята по правилу travel — она, нет — без поправки), через
    # travel_profile → .travel, на всех общих участках проверки
    for k, src in enumerate(lr.TRUCK_TIME_SOURCES):
        f = fitted[src]
        prof = lr.travel_profile(bases[src], f.params) if f.accepted else None
        mae = sum(abs((prof.travel(x[k].km, x[k].speed, x[k].city, x[k].weekday, x[k].start) if prof else x[k].current)
                      - x[k].minutes) for x in test) / len(test)
        assert c[src]['learned']['mae'] == pytest.approx(mae, abs=1e-3)
        raw = sum(abs(x[k].current - x[k].minutes) for x in test) / len(test)
        assert c[src]['raw']['mae'] == pytest.approx(raw, abs=1e-3)
        assert f == lr.fit_travel([x[k] for x in pairs], TODAY, f.model_id, lr.model_ref(bases[src]), bases[src])
    # поправка Valhalla — для его минут: своя дорожная модель и scope, время Valhalla × 1,3
    v = fitted[VALHALLA]
    assert v.kind == 'travel' and v.accepted and v.scope == VALHALLA and '+valhalla-time:' in v.model_id
    assert v.model_id == ve.road_model_id(bases[VALHALLA].roads) and lr.valid_params('travel', v.params)
    assert {f[3] for f in v.params['factors']} == {1.3}
    assert '+valhalla-time:' not in fitted[MODEL].model_id and fitted[MODEL].scope == ''
    assert o.params['corrected'] is True


def test_travel_learning_off_compares_models_as_is(bases):
    """Автообучение «Скорость машин по часам» выключено — поправки в «Развозе» не применятся: модели сравниваются «как
    есть» и поправки не учатся. Valhalla вдвое быстрее факта — «как есть» он хуже, переключения нет."""
    pairs = _pairs(scale_off)
    o, fitted = lr.fit_truck_time(pairs, TODAY, MODEL, bases, {}, correct=False)
    c = o.params['candidates']
    assert not o.accepted and o.params['source'] == MODEL and fitted == {} and o.params['corrected'] is False
    assert all(c[src]['learned'] == c[src]['raw'] for src in lr.TRUCK_TIME_SOURCES)
    assert c[VALHALLA]['raw']['mae'] > c[MODEL]['raw']['mae']
    on, _ = lr.fit_truck_time(pairs, TODAY, MODEL, bases, {})              # с поправками Valhalla выигрывает
    assert on.accepted


def test_each_model_gets_its_own_correction_before_comparison(bases):
    """Valhalla вдвое быстрее факта (поправка зоны неверна), но форма точна: «как есть» он хуже прежней модели, с
    собственной поправкой — лучше. Сравнение «как есть» выбрало бы прежнюю модель."""
    o, fitted = lr.fit_truck_time(_pairs(scale_off), TODAY, MODEL, bases, {})
    c = o.params['candidates']
    assert c[VALHALLA]['raw']['mae'] > c[MODEL]['raw']['mae']
    assert c[VALHALLA]['raw']['bias'] < 0 < c[VALHALLA]['raw']['mae']          # быстрее факта — смещение минус
    assert o.accepted and o.params['source'] == VALHALLA
    assert {f[3] for f in fitted[VALHALLA].params['factors']} == {2.0}


def test_rejects_valhalla_when_worse_or_equal(bases):
    worse, _ = lr.fit_truck_time(_pairs(model_knows), TODAY, MODEL, bases, {})
    assert not worse.accepted and worse.params['source'] == MODEL
    assert worse.reason.startswith('Valhalla-ն առնվազն 2%-ով ավելի ճշգրիտ չէ, քան նախկին մոդելը․ սխալ ')
    assert worse.reason.endswith('— մնում է նախկին մոդելը')
    same, _ = lr.fit_truck_time(_pairs(lambda rnd, km: (1.1 * km / 25 * 60, km / 25 * 60, km / 25 * 60)), TODAY,
                                MODEL, bases, {})
    c = same.params['candidates']
    assert c[MODEL] == c[VALHALLA] and not same.accepted and same.params['source'] == MODEL


@pytest.mark.parametrize('gain, accepted', [(0.015, False), (0.025, True)])
def test_needs_two_percent(bases, gain, accepted):
    o, _ = lr.fit_truck_time(_duel(VALHALLA, gain), TODAY, MODEL, bases, {})
    assert o.params['candidates'][MODEL]['learned']['mae'] == pytest.approx(1.0)
    assert o.params['candidates'][VALHALLA]['learned']['mae'] == pytest.approx(1.0 - gain)
    assert o.accepted is accepted and o.params['source'] == (VALHALLA if accepted else MODEL)


@pytest.mark.parametrize('counts, enough', [((200, 7, 60, 3), True), ((199, 7, 60, 3), False),
                                            ((200, 6, 60, 3), False), ((200, 7, 59, 3), False),
                                            ((200, 7, 60, 2), False)])
def test_minimum_data_on_the_boundary(bases, counts, enough):
    assert lr.TRUCK_TIME_MIN == (200, 7, 60, 3)
    o, _ = lr.fit_truck_time(_counted(*counts), TODAY, MODEL, bases, {})
    legs, days = o.params['legs'], o.params['days']
    assert (legs['train'], days['train'], legs['test'], days['test']) == counts
    assert o.accepted is enough and o.params['source'] == (VALHALLA if enough else MODEL)
    if not enough:
        assert o.reason.startswith('քիչ տվյալներ') and o.reason.endswith('— մնում է նախկին մոդելը')
        assert o.params['candidates'][VALHALLA]['learned']['mae'] < o.params['candidates'][MODEL]['learned']['mae']


def test_hysteresis_switches_only_by_two_percent_both_ways(bases):
    rows = []

    def run(pairs):
        incumbent = lr.truck_time_learned(rows) or MODEL
        o, _ = lr.fit_truck_time(pairs, TODAY, incumbent, bases, {})
        rows.append({'kind': 'truck_time', 'scope': '', 'accepted': o.accepted, 'params': o.params, 'reason': o.reason})
        return lr.truck_time_learned(rows) or MODEL

    assert run(_duel(VALHALLA, 0.01)) == MODEL            # Valhalla лучше на 1% — остаётся прежняя
    assert run(_duel(VALHALLA, 0.03)) == VALHALLA         # на 3% — переключение
    assert run(_duel(MODEL, 0.01)) == VALHALLA            # прежняя лучше на 1% — Valhalla остаётся: без «туда-сюда»
    assert rows[2]['params']['challenger'] == MODEL and rows[2]['reason'].endswith('— մնում է Valhalla-ն')
    assert run(_duel(VALHALLA, 0.01)) == VALHALLA
    assert run(_duel(MODEL, 0.03)) == MODEL               # обратно — тоже только на 2%
    assert rows[4]['reason'].startswith('նախկին մոդելն ավելի ճշգրիտ է, քան Valhalla-ն')
    assert [r['accepted'] for r in rows] == [False, True, False, False, True]


def test_correction_stored_with_a_switch_is_checked_against_the_one_in_effect(bases):
    """Поправка, которую сохраняют вместе с переключением, принимается как в fit_travel: только если она точнее
    поправки, что уже действует у этой модели, а не просто модели «как есть»."""
    legs = []
    for i in range(40):
        d = TODAY - timedelta(days=40 - i)
        r = 1.2 if d >= TEST_FROM else 1.1                  # неделя проверки — медленнее, чем дни обучения
        legs += [_pair(d, 9 + j % 4, 10.0 * r, 4.0, 10.0, 10.0) for j in range(10)]
    prev = {'factors': [[1, w, h, 1.2] for w in (0, 1) for h in range(9, 13)], 'ref': lr.model_ref(bases[MODEL])}
    _, with_prev = lr.fit_truck_time(legs, TODAY, VALHALLA, bases, {MODEL: prev})
    _, without = lr.fit_truck_time(legs, TODAY, VALHALLA, bases, {})
    assert with_prev[MODEL].params == without[MODEL].params                  # поправка одна и та же
    assert without[MODEL].accepted and without[MODEL].mae_before == pytest.approx(2.0, abs=1e-6)   # точнее «как есть»
    assert not with_prev[MODEL].accepted and with_prev[MODEL].mae_before == pytest.approx(0.0, abs=1e-6)


def test_incumbent_is_scored_with_the_correction_the_run_keeps(bases):
    """У модели, которой учился прогон, применится обычная строка travel (учится на всех участках, не только на общих) —
    с ней она и сравнивается: на общих участках её поправка не прошла бы, а настоящая точна — переключения нет."""
    legs = []
    for i in range(40):
        d = TODAY - timedelta(days=40 - i)
        for j in range(10):
            if d < TEST_FROM:
                legs.append(_pair(d, 9 + j % 4, 10.0, 4.0, 10.0, 10.0))
            else:                                             # прежней модели нужно × 1,1; Valhalla — ± 0,5 мин
                legs.append(_pair(d, 9 + j % 4, 11.0, 4.0, 10.0, 11.0 + (0.5 if j % 2 else -0.5)))
    shared, _ = lr.fit_truck_time(legs, TODAY, MODEL, bases, {})
    assert shared.accepted                                    # по общим участкам прежняя модель — «как есть»
    regular = lr.Outcome('travel', '', True, 'да', {'factors': [[1, w, h, 1.1] for w in (0, 1) for h in range(9, 13)],
                                                    'ref': lr.model_ref(bases[MODEL])},
                         ve.road_model_id(bases[MODEL].roads))
    o, fitted = lr.fit_truck_time(legs, TODAY, MODEL, bases, {}, kept={MODEL: regular})
    assert o.params['candidates'][MODEL]['learned']['mae'] == pytest.approx(0.0, abs=1e-9)
    assert not o.accepted and o.params['source'] == MODEL and set(fitted) == {VALHALLA}
    rejected = replace(regular, accepted=False)               # не принята — у модели остаётся действующая (её нет)
    again, _ = lr.fit_truck_time(legs, TODAY, MODEL, bases, {}, kept={MODEL: rejected})
    assert again.accepted and again.params['candidates'][MODEL]['learned'] == again.params['candidates'][MODEL]['raw']


def test_valhalla_unavailable_or_no_common_legs(bases):
    o, fitted = lr.fit_truck_time([], TODAY, VALHALLA, {MODEL: bases[MODEL]}, {})
    assert not o.accepted and o.params is None and fitted == {}
    assert o.reason.startswith('Valhalla-ն հասանելի չէ') and o.reason.endswith('— մնում է Valhalla-ն')
    empty, _ = lr.fit_truck_time([], TODAY, MODEL, bases, {}, no_valhalla=12)
    assert not empty.accepted and empty.params['legs'] == {'train': 0, 'test': 0, 'no_valhalla': 12}
    assert empty.params['candidates'] == {} and empty.reason.startswith('քիչ տվյալներ')


def test_truck_time_learned_and_valid_params():
    row = lambda src, ok=True, day='2026-09-01': {'kind': 'truck_time', 'scope': '', 'run_day': day,  # noqa: E731
                                                  'accepted': ok, 'params': {'source': src}}
    assert lr.truck_time_learned([]) is None
    assert lr.truck_time_learned([row(VALHALLA), row(MODEL, ok=False)]) == VALHALLA        # отклонённая — не выбор
    assert lr.truck_time_learned([row(VALHALLA), row(MODEL)]) == MODEL
    assert lr.truck_time_learned([row(VALHALLA), row('yandex')]) == VALHALLA             # битая строка — не действует
    assert lr.truck_time_learned([{**row(VALHALLA), 'kind': 'travel'}]) is None
    assert not lr.valid_params('truck_time', None) and not lr.valid_params('truck_time', {'source': 'x'})


# ============================== участки: общие и симметричный отбор ==============================

def _leg(a, b, start, minutes, clean=True):
    depart = datetime(2026, 9, 29, 9, 0, tzinfo=TZ) + timedelta(minutes=start)
    return ac.Leg('A', 'B', a, b, depart, depart + timedelta(minutes=minutes), 1.0, clean)


def test_truck_time_obs_pairs_only_common_legs_and_filters_symmetrically(bases):
    norms = bases[MODEL]
    truck = norms.roads

    def raw(a, b):
        city = in_city(a, norms.city_center, norms.city_radius_km) and in_city(b, norms.city_center,
                                                                               norms.city_radius_km)
        km = norms.km(a, b)
        return km, km / (norms.speed_city_kmh if city else norms.speed_region_kmh) * 60, truck.valhalla_minutes(
            a, b, city)

    _, m01, v01 = raw(P[0], P[1])            # город, на север: у подделки Valhalla вдвое медленнее модели
    _, m14, v14 = raw(P[1], P[4])            # в область, на север: в 3,7 раза
    lo, hi = lr.LEG_RATIO_OUTLIER
    legs = (_leg(P[0], P[1], 0, m01),                       # общий
            _leg(P[1], P[2], 20, raw(P[1], P[2])[1]),       # общий
            _leg(P[2], NEW, 40, 10.0),                      # у Valhalla нет минут — ни в одну модель
            _leg(P[0], P[1], 60, m01, clean=False),         # не чистый — не участок
            _leg(P[0], P[1], 80, 0.21 * m01),               # факт / модель 0,21 — годится, факт / Valhalla 0,1 — нет
            _leg(P[1], P[4], 100, 5.5 * m14),               # факт / модель 5,5 — нет, факт / Valhalla 1,5 — годится
            _leg(P[1], P[4], 120, 4.9 * m14))               # 4,9 модели и 1,3 Valhalla — годится обеим
    assert 0.21 * m01 / v01 < lo and lo <= 5.5 * m14 / v14 <= hi and lo <= 4.9 * m14 / v14 <= hi
    actual = ac.DayActual(0, 0.0, None, None, legs=legs)
    pairs, missing = lr.truck_time_obs(date(2026, 9, 29), actual, norms)
    assert missing == 1 and len(pairs) == 3
    for (m, v), g in zip(pairs, (legs[0], legs[1], legs[6])):
        assert (m.minutes, m.start, m.km, m.day, m.hour) == (v.minutes, v.start, v.km, v.day, v.hour)
        assert m.minutes == pytest.approx(g.minutes) and m.km == pytest.approx(norms.km(g.pa, g.pb))
        km, model, valhalla = raw(g.pa, g.pb)
        assert (m.model, v.model) == (pytest.approx(model), pytest.approx(valhalla))
        assert (m.current, v.current) == (pytest.approx(model), pytest.approx(valhalla))   # профиля пробок нет
        assert v.speed == pytest.approx(km / valhalla * 60)
    # часовой профиль норм — в «как есть» (current), не в модель, по которой учится поправка
    slow = replace(norms, traffic=TrafficProfile({(True, 0, 9): 0.5, (False, 0, 9): 0.5}))   # вт 9:00–10:00
    again, _ = lr.truck_time_obs(date(2026, 9, 29), actual, slow)
    assert [(m.model, v.model) for m, v in again] == [(m.model, v.model) for m, v in pairs]
    assert again[0][0].current == pytest.approx(2 * pairs[0][0].model)
    assert again[0][1].current == pytest.approx(2 * pairs[0][1].model)


def test_leg_obs_model_minutes_follow_the_truck_time(bases):
    """Участки обучения (travel) — в модели, которой «Развоз» считает грузовики: у Valhalla — его минуты."""
    actual = ac.DayActual(0, 0.0, None, None, legs=(_leg(P[0], P[1], 0, 9.0),))
    day = date(2026, 9, 29)
    plain, = lr.leg_obs(day, actual, bases[MODEL])
    valh, = lr.leg_obs(day, actual, bases[VALHALLA])
    city = True
    assert plain.model == pytest.approx(plain.km / NORMS.speed_city_kmh * 60) and plain.speed == NORMS.speed_city_kmh
    assert valh.model == pytest.approx(bases[VALHALLA].roads.valhalla_minutes(P[0], P[1], city))
    assert valh.speed == pytest.approx(valh.km / valh.model * 60) and valh.km == plain.km


def test_valhalla_correction_does_not_depend_on_zone_speeds(bases):
    """Минуты Valhalla от скоростей зон не зависят: выученный множитель применяется как есть (1 / r), даже если
    скорости в настройках (или калибровка по GPS менеджеров) сменились; у прежней модели — пересчитывается."""
    ref = lr.model_ref(bases[MODEL])                         # учились при 20 км/ч в городе
    travel = {'factors': [[1, 0, 9, 1.5]], 'ref': ref}
    for src, want in ((VALHALLA, 1 / 1.5), (MODEL, 20.0 / (33.0 * 1.5))):
        now = replace(bases[src], speed_city_kmh=33.0)       # теперь в городе 33 км/ч
        assert lr.speed_factors(travel, now)[(True, 0, 9)] == pytest.approx(want)
    prev = {'factors': [[1, 0, 15, 1.4]], 'ref': {**ref, 'speed_city_kmh': 10.0}}
    own = lr._travel_params({(True, 0, 9): 1.2}, ref, prev, True)
    other = lr._travel_params({(True, 0, 9): 1.2}, ref, prev, False)
    assert {(c, w, h): r for c, w, h, r in own['factors']}[(1, 0, 15)] == 1.4             # перенос — как есть
    assert {(c, w, h): r for c, w, h, r in other['factors']}[(1, 0, 15)] == pytest.approx(2.8)   # к новым скоростям


# ============================== порядок источников и срез ==============================

def test_truck_time_source_precedence(monkeypatch):
    assert ve.truck_time_source(None) == (MODEL, 'default')
    assert ve.truck_time_source(VALHALLA) == (VALHALLA, 'learned')
    assert ve.truck_time_source(MODEL) == (MODEL, 'learned')
    assert ve.truck_time_source('yandex') == (MODEL, 'default')                 # не модель — не выбор
    monkeypatch.setenv('ROUTES_TRUCK_TIME', '  ')
    assert ve.truck_time_source(VALHALLA) == (VALHALLA, 'learned')              # пусто — не задана
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'model')
    assert ve.truck_time_source(VALHALLA) == (MODEL, 'env')                     # задана — главнее выбора
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'Valhalla')
    assert ve.truck_time_source(None) == (VALHALLA, 'env')
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'nonsense')
    assert ve.truck_time_source(VALHALLA) == (MODEL, 'env')                     # неизвестное — прежняя модель
    journal = ([{'kind': 'truck_time', 'scope': '', 'accepted': True, 'params': {'source': VALHALLA}}], {})
    monkeypatch.delenv('ROUTES_TRUCK_TIME')
    assert views._truck_time_choice(journal) == (VALHALLA, 'learned')
    assert views._truck_time_choice((journal[0], {'truck_time': False})) == (MODEL, 'default')   # галочка снята
    assert views._truck_time_choice(None) == (MODEL, 'default')                                   # журнал не прочитан
    assert lr.DEFAULT_AUTO['truck_time'] is True and 'truck_time' in lr.KINDS


def test_truck_slice_switch_shares_frozen_snapshot_threadsafe(tmp_path, fake):
    reg, cost = _registry(tmp_path), ve.truck_costing(3000)
    reg.matrix(ve.PROFILE_TRUCK, cost).ensure(P[:4])
    view = ve.ValhallaRoads(reg, ve.PROFILE_CAR, FakeOsm(missing=()), time_only=True, truck_cost=cost)
    model = view.truck()
    on = view.truck(truck_time=True)
    assert on is not model and on.truck() is on and view.truck() is model and not model.truck_time
    assert on._table is model._table and on.active                          # тот же срез матриц
    a, b = P[0], P[1]
    assert model.minutes(a, b, True) is None and on.minutes(a, b, True) == model.valhalla_minutes(a, b, True)
    assert on.km(a, b) == model.km(a, b)
    assert ve.road_model_id(on) == ve.road_model_id(model) + f'+valhalla-time:test:truck:{ve.costing_key(cost)}' + TF
    back = on.truck(truck_time=False)
    assert back is not on and ve.road_model_id(back) == ve.road_model_id(model) and on.truck_time
    reg.matrix(ve.PROFILE_TRUCK, cost).ensure(P)                            # фон досчитал точки после среза
    assert on.minutes(P[0], P[4], False) is None and view.truck(truck_time=True).minutes(P[0], P[4], False) is None
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda k: view.truck(truck_time=bool(k % 2)), range(200)))
    assert all(g.truck_time is bool(k % 2) for k, g in enumerate(got))
    assert view.truck() is model and not model.truck_time and not view.truck_time
    norms = replace(NORMS, roads=view)
    assert norms.for_trucks().roads is model and norms.for_trucks(truck_time=False).roads is model
    assert norms.for_trucks(truck_time=True).roads.serves_minutes
    graph = rd.RoadDistances('memory', lambda: None)
    assert replace(NORMS, roads=graph).for_trucks(truck_time=True).roads is graph    # граф времени не знает
    assert NORMS.for_trucks(truck_time=True) is NORMS


def test_provider_takes_the_callers_choice_and_needs_truck_matrix(tmp_path, fake, monkeypatch):
    provider, _ = _provider(tmp_path, monkeypatch)
    osm = FakeOsm(missing=())
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'valhalla')      # выбор передан — провайдер переменную не читает
    off = _wait(lambda: provider.get(osm, P, 3000, truck_time=False))
    assert off is not None and not off.truck().truck_time and '+valhalla-time:' not in ve.road_model_id(off.truck())
    assert _wait(lambda: provider.get(osm, P, 3000)).truck().truck_time      # не передан — как раньше, переменная
    monkeypatch.delenv('ROUTES_TRUCK_TIME')
    on = _wait(lambda: provider.get(osm, P, 3000, truck_time=True))
    assert on.truck().serves_minutes and '+valhalla-time:' in ve.road_model_id(on.truck())
    assert _wait(lambda: not _preparers())
    # матрица грузовика не считается — машинам Valhalla есть, грузовикам с минутами Valhalla — граф OSM
    real = FakeActor.matrix

    def no_truck(self, body):
        if body['costing'] == 'truck':
            raise RuntimeError('truck engine broken')
        return real(self, body)
    monkeypatch.setattr(FakeActor, 'matrix', no_truck)
    broken, _ = _provider(tmp_path / 'b', monkeypatch)
    assert _wait(lambda: broken.get(osm, P, 3000, truck_time=False)) is not None
    assert _wait(lambda: not _preparers())
    assert broken.get(osm, P, 3000, truck_time=True) is None


# ============================== «Развоз»: применение ==============================

class FakeProvider:
    """Подделка ValhallaProvider без фона: матрицы считает сразу (подделка движка), срез — как у настоящего;
    truck_time — выбор вызывающего, None — ROUTES_TRUCK_TIME. asked — что просили."""

    def __init__(self, folder):
        self.reg = _registry(folder)
        self.asked = []

    def get(self, fallback, points, truck_capacity_kg=None, truck_time=None):
        self.asked.append(truck_time)
        pts = [p for p in points if p is not None]
        cost = ve.truck_costing(truck_capacity_kg)
        self.reg.matrix(ve.PROFILE_CAR, ve.CAR_COSTING).ensure(pts)
        self.reg.matrix(ve.PROFILE_TRUCK, cost).ensure(pts)
        if truck_time is None:
            truck_time = ve.truck_time_mode() == ve.TRUCK_TIME_VALHALLA
        return ve.ValhallaRoads(self.reg, ve.PROFILE_CAR, fallback, time_only=True, truck_time=truck_time,
                                truck_cost=cost)


POINTS = [(40.18, 44.50), (40.19, 44.52), (40.20, 44.55)]       # магазины 101, 102, 104 снимка


def _valhalla_client(client, tmp_path):
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2), _dorder(3, 104, 1200.0, agent=2)])
    state = client.application.extensions['route_optimizer']
    state.valhalla = FakeProvider(tmp_path / 'valhalla')
    return state


def _ctx(state, day=date(2026, 10, 1)):
    snap, _ = state.snapshots.cached()
    bundle = views._bundle(state)
    return views._dispatch_ctx(state, snap, bundle, day, views._ready_trucks(snap, bundle), POINTS, {})


def _decision(source, day, accepted=True):
    return day, [lr.Outcome('truck_time', '', accepted, 'тест', {'source': source})]


def _build(client):
    """«Собрать рейсы» — план целиком, кроме времени сборки и номеров рейсов (у каждой сборки — свои)."""
    r = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']})
    assert r.status_code == 200, r.get_json()
    plan = r.get_json()['plan']
    plan.pop('built_at')
    for t in plan['trucks']:
        for trip in t['trips']:
            trip.pop('id')
    return plan


def test_dispatch_uses_chosen_truck_time_with_env_and_switch_precedence(client, fake, tmp_path, monkeypatch):
    state = _valhalla_client(client, tmp_path)
    model_ctx = _ctx(state)
    model_id = ve.road_model_id(model_ctx.norms.roads)
    assert state.valhalla.asked[-1] is False and '+valhalla-time:' not in model_id
    a = _build(client)
    state.store.save_learned(*_decision(VALHALLA, '2026-09-30'))
    v_ctx = _ctx(state)
    v_id = ve.road_model_id(v_ctx.norms.roads)
    assert state.valhalla.asked[-1] is True and v_id.startswith(model_id + '+valhalla-time:')   # км — те же
    depot = model_ctx.depot
    d0, m0 = fl._matrices(POINTS, depot, model_ctx.norms)
    d1, m1 = fl._matrices(POINTS, depot, v_ctx.norms)
    assert d1 == d0 and m1 != m0
    city = in_city(depot, v_ctx.norms.city_center, 12.0) and in_city(POINTS[0], v_ctx.norms.city_center, 12.0)
    assert m1[0][1] == pytest.approx(v_ctx.norms.roads.valhalla_minutes(depot, POINTS[0], city))
    assert m0[0][1] == pytest.approx(d0[0][1] / model_ctx.norms.speed_city_kmh * 60)
    b = _build(client)
    assert b != a and [t['minutes'] for t in b['trucks']] != [t['minutes'] for t in a['trucks']]
    state.store.save_learned(*_decision(MODEL, '2026-10-01'))                    # выбор вернулся к прежней модели
    assert _build(client) == a                                                   # те же рейсы до бита
    state.store.save_learned(*_decision(VALHALLA, '2026-10-02'))
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'model')
    assert _build(client) == a                                                   # переменная главнее выбора
    monkeypatch.delenv('ROUTES_TRUCK_TIME')
    assert _build(client) == b
    assert client.post('/api/routes/learning/auto', json={'kind': 'truck_time', 'auto': False}).status_code == 200
    assert _build(client) == a                                                   # галочка снята — прежняя модель
    monkeypatch.setenv('ROUTES_TRUCK_TIME', 'valhalla')
    assert _build(client) == b                                                   # переменная — и без галочки


def test_travel_correction_applies_only_to_its_model(client, fake, tmp_path):
    state = _valhalla_client(client, tmp_path)
    plain = _ctx(state).norms
    model_id, valhalla_id = (ve.road_model_id(plain.for_trucks(truck_time=t).roads) for t in (False, True))
    ref = lr.model_ref(plain)

    def travel(r, mid, scope=''):
        return [lr.Outcome('travel', scope, True, 'да', {'factors': [[1, 0, 9, r]], 'ref': ref}, mid)]
    state.store.save_learned('2026-09-20', travel(2.0, model_id))
    assert _ctx(state).norms.traffic.factor(True, 3, 9 * 60) == pytest.approx(1 / 2.0)
    state.store.save_learned(*_decision(VALHALLA, '2026-09-21'))
    assert _ctx(state).norms.traffic is None                                     # поправка прежней модели — не Valhalla
    # строка с id минут Valhalla, но scope '' — выучена по скорости зоны (ROUTES_TRUCK_TIME=valhalla до выбора
    # модели): к минутам Valhalla не применяется
    state.store.save_learned('2026-09-22', travel(0.7, valhalla_id))
    assert _ctx(state).norms.traffic is None
    state.store.save_learned('2026-09-22', travel(0.5, valhalla_id, VALHALLA))   # тот же день — рядом, свой scope
    v = _ctx(state).norms
    assert v.traffic.factor(True, 3, 9 * 60) == pytest.approx(1 / 0.5) and v.traffic.report['trucks'] == 'learned'
    state.store.save_learned(*_decision(MODEL, '2026-09-23'))
    assert _ctx(state).norms.traffic.factor(True, 3, 9 * 60) == pytest.approx(1 / 2.0)   # и наоборот
    assert [(r['scope'], r['model_id']) for r in state.store.learned() if r['run_day'] == '2026-09-22'] == \
        [('', valhalla_id), (VALHALLA, valhalla_id)]


# ============================== ночной прогон ==============================

STORES = {201: (40.2050, 44.5650), 202: (40.1990, 44.5500), 203: (40.2120, 44.5800), 204: (40.1700, 44.5200),
          205: (40.2900, 44.6500), 206: (40.1850, 44.4800), 207: (40.1000, 44.7000), 208: (40.3000, 44.4500),
          209: (40.2200, 44.5300), 210: (40.1500, 44.6000)}
TRIPS = ((201, 202, 203, 204, 209), (205, 206, 207, 208, 210))   # 12 участков в день: 84 на неделе проверки
CITY_CENTER = (40.1792, 44.4991)


class ValhallaFacts:
    """Факт CAR1: два рейса в день по 5 магазинов (город и область); каждый переезд — factor × минуты
    Valhalla-грузовика подделки движка (на север — медленнее): Valhalla со своей поправкой знает время участка,
    прежняя модель (км / скорость зоны) — нет."""

    def __init__(self, days, factor=1.1):
        self.days, self.factor = days, factor

    def car_days(self, since, until):
        return [('CAR1', d.isoformat()) for d in self.days if since <= d.isoformat() <= until]

    def minutes(self, a, b):
        city = in_city(a, CITY_CENTER, 12.0) and in_city(b, CITY_CENTER, 12.0)
        return self.factor * round(_metric(a, b, 'truck')[1]) / 60.0 * ve.TIME_FACTOR[city]

    def day(self, car, ds):
        d = date.fromisoformat(ds)
        tr = Track(DP_DEPOT, datetime(d.year, d.month, d.day, 8, 30, tzinfo=TZ)).stay(20)
        pos = DP_DEPOT
        for trip in TRIPS:
            for p in [*(STORES[c] for c in trip), DP_DEPOT]:
                tr.drive(p, haversine_km(pos, p) / self.minutes(pos, p) * 60.0).stay(6 if p != DP_DEPOT else 25)
                pos = p
        track = [(int(f.at.timestamp() * 1000), f.lat, f.lon, f.accuracy, None) for f in tr.fixes]
        stops = [{'stop_id': f'S:{cid}', 'customer_id': cid, 'lat': p[0], 'lon': p[1], 'weight_kg': 300.0, 'seq': i,
                  'delivered_share': 1.0} for i, (cid, p) in enumerate(sorted(STORES.items()), 1)]
        return {'track': track, 'stops': stops}

    def refuels(self):
        return []

    def version(self, car, ds):
        return (ds,)


def _facts_client(client, tmp_path, monkeypatch):
    state = _valhalla_client(client, tmp_path)
    state.fleet_facts = ValhallaFacts([TODAY - timedelta(days=i) for i in range(31, 0, -1)])
    monkeypatch.setattr(views, '_clock', lambda: datetime(2026, 10, 3, 10, 0))
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 10, 3, 10, 0, tzinfo=TZ))
    return state


def _travels(outcomes):
    """Строки travel прогона по scope: '' — поправка прежней модели, 'valhalla' — минут Valhalla."""
    return {o.scope: o for o in outcomes if o.kind == 'travel'}


def test_nightly_switches_to_valhalla_with_its_correction_idempotent_no_flapping(client, fake, tmp_path, monkeypatch):
    state = _facts_client(client, tmp_path, monkeypatch)
    out = views.run_learning(state, TODAY)
    tt, travels = next(o for o in out if o.kind == 'truck_time'), _travels(out)
    assert tt.accepted and tt.params['source'] == VALHALLA, tt.reason
    legs, c = tt.params['legs'], tt.params['candidates']
    assert legs['train'] >= 200 and legs['test'] >= 60 and legs['no_valhalla'] == 0
    assert c[VALHALLA]['learned']['mae'] <= 0.98 * c[MODEL]['learned']['mae']
    # обе строки travel дня: обычная — прежней модели (ею учился прогон) и поправка выбранной Valhalla — дня без
    # поправки нет
    assert set(travels) == {'', VALHALLA} and '+valhalla-time:' not in travels[''].model_id
    v = travels[VALHALLA]
    assert '+valhalla-time:' in v.model_id and v.accepted and v.reason.startswith('ընտրված մոդելի համար («Valhalla»)՝ ')
    assert all(0.8 < f[3] < 1.3 for f in v.params['factors'])                 # факт ≈ 1,1 × Valhalla
    assert views.run_learning(state, TODAY) == out                               # повтор дня — тот же итог
    ctx = _ctx(state, date(2026, 10, 4))
    assert '+valhalla-time:' in ve.road_model_id(ctx.norms.roads) and ctx.norms.traffic.report['trucks'] == 'learned'
    st = {s['kind']: s for s in client.get('/api/routes/learning/status').get_json()['status']}
    assert st['truck_time']['source'] == {'value': VALHALLA, 'why': 'learned', 'learned': VALHALLA}
    assert st['travel']['scope'] == VALHALLA and st['travel']['in_effect']['model_id'] == v.model_id
    nxt = views.run_learning(state, TODAY + timedelta(days=1))
    ntt = next(o for o in nxt if o.kind == 'truck_time')
    assert not ntt.accepted and ntt.params['source'] == VALHALLA and ntt.params['challenger'] == MODEL   # гистерезис
    assert set(_travels(nxt)) == {VALHALLA}                                      # обычная поправка — уже для Valhalla


def test_nightly_same_day_rerun_without_valhalla_keeps_the_switch(client, fake, tmp_path, monkeypatch):
    """Утром модель сменилась; сервер перезапустили — Valhalla ещё не готов, а владелец нажал «Пересчитать»:
    решение и поправка Valhalla этого дня не затираются (сбой инфраструктуры — не повод переключаться обратно)."""
    state = _facts_client(client, tmp_path, monkeypatch)
    views.run_learning(state, TODAY)
    provider, state.valhalla = state.valhalla, None
    again = views.run_learning(state, TODAY)
    assert not any(o.kind == 'truck_time' for o in again) and set(_travels(again)) == {''}
    rows = state.store.learned()
    assert lr.truck_time_learned(rows) == VALHALLA
    assert any(r['scope'] == VALHALLA and r['accepted'] for r in rows if r['kind'] == 'travel')
    state.valhalla = provider                                                    # Valhalla снова готов
    ctx = _ctx(state, date(2026, 10, 4))
    assert '+valhalla-time:' in ve.road_model_id(ctx.norms.roads) and ctx.norms.traffic.report['trucks'] == 'learned'


@pytest.mark.parametrize('pin', ['env', 'auto'])
def test_nightly_choice_not_in_effect_keeps_its_correction(client, fake, tmp_path, monkeypatch, pin):
    """Выбор обучения сейчас не действует (переменная ROUTES_TRUCK_TIME=model или снята галочка) — поправка выбранной
    Valhalla всё равно в журнале: убрали переменную / вернули галочку — «Развоз» сразу считает с ней."""
    state = _facts_client(client, tmp_path, monkeypatch)
    if pin == 'env':
        monkeypatch.setenv('ROUTES_TRUCK_TIME', 'model')
    else:
        state.store.save_learning_auto('truck_time', False, 'qa')
    out = views.run_learning(state, TODAY)
    tt, travels = next(o for o in out if o.kind == 'truck_time'), _travels(out)
    assert tt.accepted and tt.params['source'] == VALHALLA
    assert '+valhalla-time:' not in travels[''].model_id                       # «Развоз» — прежняя модель
    assert travels[VALHALLA].accepted and '+valhalla-time:' in travels[VALHALLA].model_id
    assert '+valhalla-time:' not in ve.road_model_id(_ctx(state).norms.roads)
    st = {s['kind']: s for s in client.get('/api/routes/learning/status').get_json()['status']}
    assert st['truck_time']['source'] == {'value': MODEL, 'why': 'env' if pin == 'env' else 'default',
                                          'learned': VALHALLA}
    assert st['travel']['scope'] == ''
    if pin == 'env':
        monkeypatch.delenv('ROUTES_TRUCK_TIME')
    else:
        state.store.save_learning_auto('truck_time', True, 'qa')
    ctx = _ctx(state, date(2026, 10, 4))
    assert '+valhalla-time:' in ve.road_model_id(ctx.norms.roads) and ctx.norms.traffic.report['trucks'] == 'learned'


def test_nightly_scores_its_model_with_the_regular_correction(client, fake, tmp_path, monkeypatch):
    """Модель, которой учился прогон, сравнивается с обычной строкой travel этого прогона (её и применят)."""
    state = _facts_client(client, tmp_path, monkeypatch)
    seen = {}
    real = lr.fit_truck_time

    def spy(*args, **kwargs):
        seen['kept'] = kwargs.get('kept', args[7] if len(args) > 7 else None)
        return real(*args, **kwargs)
    monkeypatch.setattr(lr, 'fit_truck_time', spy)
    out = views.run_learning(state, TODAY)
    assert seen['kept'] == {MODEL: _travels(out)['']}


def test_nightly_travel_learning_off_compares_as_is(client, fake, tmp_path, monkeypatch):
    state = _facts_client(client, tmp_path, monkeypatch)
    state.store.save_learning_auto('travel', False, 'qa')
    out = views.run_learning(state, TODAY)
    tt = next(o for o in out if o.kind == 'truck_time')
    assert tt.params['corrected'] is False and set(_travels(out)) == {''}     # поправки не учатся для выбора
    c = tt.params['candidates']
    assert all(c[m]['learned'] == c[m]['raw'] for m in lr.TRUCK_TIME_SOURCES)


def test_nightly_without_valhalla_explains_and_keeps_model(client, monkeypatch, tmp_path):
    state = _facts_client(client, tmp_path, monkeypatch)
    state.valhalla = None
    by = {o.kind: o for o in views.run_learning(state, TODAY)}
    assert not by['truck_time'].accepted and by['truck_time'].reason.startswith('Valhalla-ն հասանելի չէ')
    assert by['travel'].model_id == 'straight'


# ============================== журнал и страница ==============================

def test_store_migrates_13_to_14_keeps_rows_and_ids(tmp_path):
    path = str(tmp_path / 'v13.db')
    s = rst.Store(path)
    s.save_learned('2026-10-01', [lr.Outcome('unload', '', True, 'да', {'per_stop_min': 5.0, 'per_tonne_min': 9.0,
                                                                        'store_offsets': {}}, n_obs=40, n_test=10),
                                  lr.Outcome('fuel', 'CAR1', False, 'мало данных')])
    s.save_learning_auto('travel', False, 'qa')
    with closing(sqlite3.connect(path)) as conn:                                # база схемы 13: прежний CHECK вида
        conn.execute('ALTER TABLE learned_norms RENAME TO learned_old')
        conn.execute(rst._LEARNED_TABLE_V13)
        conn.execute(f'INSERT INTO learned_norms({rst._LEARNED_COPY}) SELECT {rst._LEARNED_COPY} FROM learned_old')
        conn.execute('DROP TABLE learned_old')
        conn.execute("UPDATE sqlite_sequence SET seq = 50 WHERE name = 'learned_norms'")   # повторы дня расходуют id
        conn.execute("UPDATE meta SET value = '13' WHERE key = 'schema_version'")
        conn.commit()
        before = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO learned_norms(kind, scope, run_day, n_obs, n_test, accepted, reason, created_at) "
                         "VALUES('truck_time', '', '2026-10-02', 0, 0, 0, 'x', 'now')")
    s2 = rst.Store(path)
    s2.save_learned('2026-10-02', [lr.Outcome('truck_time', '', True, 'да', {'source': VALHALLA})])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(rst.SCHEMA_VERSION),)                                          # 13 → 14 → … → текущая
        rows = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        # шаг журнала обучения (№61) добавил столбец confidence (у прежних строк — NULL)
        assert [r[:-1] for r in rows[:len(before)]] == before and all(r[-1] is None for r in rows[:len(before)])
        assert rows[-1][1] == 'truck_time' and rows[-1][0] == 51   # id не повторяются
        assert conn.execute("SELECT name FROM sqlite_sequence WHERE name LIKE 'learned_norms%'").fetchall() == \
            [('learned_norms',)]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO learned_norms(kind, scope, run_day, n_obs, n_test, accepted, reason, created_at) "
                         "VALUES('other', '', '2026-10-02', 0, 0, 0, 'x', 'now')")
    assert s2.learning_auto() == {'travel': False}
    assert lr.truck_time_learned(s2.learned(accepted_only=True)) == VALHALLA


def test_learning_page_shows_truck_time_model(client, fake, tmp_path, monkeypatch):
    state = _valhalla_client(client, tmp_path)
    d = client.get('/api/routes/learning').get_json()
    tt = next(s for s in d['status'] if s['kind'] == 'truck_time')
    assert tt['title'] == 'Բեռնատարների ճանապարհի ժամանակը՝ մոդել' and tt['auto'] and tt['default_auto']
    assert tt['source'] == {'value': MODEL, 'why': 'default', 'learned': None} and tt['last'] is None
    assert d['rules']['truck_time_min'] == list(lr.TRUCK_TIME_MIN)
    params = {'source': VALHALLA, 'challenger': VALHALLA, 'legs': {'train': 240, 'test': 70, 'no_valhalla': 0},
              'days': {'train': 24, 'test': 7},
              'candidates': {m: {'raw': {'mae': 1.6, 'bias': 0.8}, 'learned': {'mae': 1.2, 'bias': 0.1}}
                             for m in lr.TRUCK_TIME_SOURCES}}
    state.store.save_learned('2026-10-02', [lr.Outcome('truck_time', '', True, '<b>принято</b>', params)])
    tt = next(s for s in client.get('/api/routes/learning/status').get_json()['status'] if s['kind'] == 'truck_time')
    assert tt['source'] == {'value': VALHALLA, 'why': 'learned', 'learned': VALHALLA}
    assert tt['in_effect']['params'] == params and tt['last']['reason'] == '<b>принято</b>'   # экранирует страница
    r = client.post('/api/routes/learning/auto', json={'kind': 'truck_time', 'auto': False}).get_json()
    tt = next(s for s in r['status'] if s['kind'] == 'truck_time')
    assert tt['in_effect'] is None and tt['source'] == {'value': MODEL, 'why': 'default', 'learned': VALHALLA}
    import app_v2
    from flask import render_template
    with app_v2.app.test_request_context('/routes/learning'):
        html = render_template('routes_learning.html')
    assert 'Բեռնատարների ճանապարհի ժամանակը՝ մոդել' in html and 'id="lrTtRows"' in html and 'routes_learning.js?v=18' in html
    js = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    assert 'renderTruckTime' in js and "esc(last ? 'Համեմատություն դեռ չկա՝ ' + last.reason" in js

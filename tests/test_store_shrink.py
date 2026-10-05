"""Время магазина: сглаживание к группе (№66, docs/plans/learning-pro-plan.md §2) — оценка k, опора (введённое > группа >
a, сети — своей группой), выбор проверкой с гистерезисом, применение в «Развозе», подсказки, синтетика против №60."""
import hashlib
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import replace
from types import SimpleNamespace
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, variance

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import erp  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_learning_loop import TODAY, _learning_client, _unload_obs  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, client  # noqa: E402,F401
from test_route_store_unload import GOLDEN, GOLDEN_KG, GOLDEN_POINTS, _build, _js_hints, _plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# действующая строка: сглаживание, k = 4, оценён 3 дня назад
SHRINK_ON = {'store_rule': {'rule': 'shrink', 'k': 4.0, 'k_day': (TODAY - timedelta(days=3)).isoformat()},
             'store_shrink': {}}


def _current(rule):
    """Действующая строка unload с правилом rule (гистерезис fit_unload)."""
    return SHRINK_ON if rule == 'shrink' else {'store_rule': {'rule': rule, 'k': 4.0}}


def _row(rule='shrink', k=4.0, shrink=None, **extra):
    """Строка unload со сглаживанием: a = 6, b = 10; store_stats — как у №60 (факт тех же магазинов)."""
    shrink = shrink if shrink is not None else {'101': [1, 30.0, 10.0], '102': [3, 20.0, 10.0], '103': [12, 40.0, 10.0]}
    return {'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {},
            'store_stats': {c: v[:2] for c, v in shrink.items() if v[0] >= lr.STORE_MIN_OBS},
            'store_shrink': shrink, 'store_rule': {'rule': rule, 'k': k}, **extra}


# ============================== k: метод моментов ==============================

def _stores(means, n=4, spread=(-3.0, -1.0, 1.0, 3.0)):
    """Магазин → n визитов: его среднее ± spread (внутри — дисперсия var(spread))."""
    return {100 + i: [m + spread[j % len(spread)] for j in range(n)] for i, m in enumerate(means)}


def test_shrink_k_is_within_over_between_variance():
    """k = σ²_внутри / τ²_между: σ² — объединённая дисперсия визитов около среднего магазина, τ² — дисперсия средних
    около среднего группы минус шум среднего σ²/n. Пересчёт независимой формулой (statistics, равные n)."""
    means = [0.0, 2.0, 4.0, 1.0, 3.0, 5.0, 1.5, 2.5]
    res = _stores(means)
    groups = {c: 'small' for c in res}
    sigma2 = mean(variance(rs) for rs in res.values())
    tau2 = variance(means) - sigma2 / 4
    assert 1 < sigma2 / tau2 < 20 and lr.shrink_k(res, groups) == pytest.approx(sigma2 / tau2, abs=0.006)
    # центр — своя группа: сдвиг всей группы (сети на 20 мин дольше) k не меняет; одной группой тот же сдвиг —
    # «различие магазинов» (τ² растёт): k меньше
    shifted = {c: [r + (20.0 if c % 2 else 0.0) for r in rs] for c, rs in res.items()}
    by_parity = {c: ('chain:017' if c % 2 else 'small') for c in res}
    k_two = lr.shrink_k(res, by_parity)
    assert lr.shrink_k(shifted, by_parity) == pytest.approx(k_two, abs=1e-9)
    assert lr.shrink_k(shifted, groups) < k_two


def test_shrink_k_bounds_and_too_little_data():
    lo, hi = lr.SHRINK_K_BOUNDS
    same = _stores([5.0] * 8)                                   # магазины не различаются — всё шум: τ² ≤ 0
    assert lr.shrink_k(same, {c: 'small' for c in same}) == hi
    far = _stores([0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0])   # различие огромно, шум мал
    assert lr.shrink_k(far, {c: 'small' for c in far}) == lo
    seven = _stores([0.0, 4.0, 9.0, 1.0, 6.0, 12.0, 3.0])       # магазинов меньше SHRINK_K_MIN[0]
    assert lr.shrink_k(seven, {c: 'small' for c in seven}) is None
    thin = _stores([0.0, 4.0, 9.0, 1.0, 6.0, 12.0, 3.0, 8.0], n=3)   # степеней свободы внутри 8 × 2 = 16 < 20
    assert lr.shrink_k(thin, {c: 'small' for c in thin}) is None
    ok = _stores([0.0, 4.0, 9.0, 1.0, 6.0, 12.0, 3.0, 8.0, 2.0, 7.0], n=3)   # 10 × 2 = 20 — уже можно
    assert lr.shrink_k(ok, {c: 'small' for c in ok}) is not None
    alone = _stores([0.0, 4.0, 9.0, 1.0, 6.0, 12.0, 3.0, 8.0])
    # группы меньше SHRINK_GROUP_MIN — своей опоры нет, магазины сглаживаются к a: разброс — около a (остаток 0)
    assert lr.shrink_k(alone, {c: f'chain:{c}' for c in alone}) == lr.shrink_k(alone, {c: f'g{c // 2}' for c in alone})
    # магазины с одним визитом в оценку не входят (ни внутри, ни между)
    plus_one = {**alone, **{900 + i: [50.0 * i] for i in range(10)}}
    assert lr.shrink_k(plus_one, {c: 'small' for c in plus_one}) == lr.shrink_k(alone, {c: 'small' for c in alone})


def test_shrink_k_measures_spread_around_prior_used():
    """№66, ревью M2: разброс средних — около той опоры, к которой сглаживают. Магазины без своей группы (группы меньше
    SHRINK_GROUP_MIN) — около a (остаток 0, по степени свободы на магазин): их уровень далеко от a — k меньше (к a
    тянуть слабее), чем если бы у них была своя группа. Шум средних — σ²·(Σ_g (m_g − 1)/m_g·Σ 1/n + Σ_a 1/n) / df."""
    means = [10.0, 12.0, 14.0, 11.0, 13.0, 15.0, 11.5, 12.5]
    res = _stores(means)
    sigma2 = mean(variance(rs) for rs in res.values())
    own = lr.shrink_k(res, {c: 'small' for c in res})                           # одна группа со своей опорой
    to_a = lr.shrink_k(res, {c: f'chain:{c}' for c in res})                     # каждый — к a
    assert own == pytest.approx(sigma2 / (variance(means) - sigma2 / 4), abs=0.006)
    tau2_a = mean(m * m for m in means) - sigma2 / 4
    assert to_a == pytest.approx(max(lr.SHRINK_K_BOUNDS[0], sigma2 / tau2_a), abs=0.006) and to_a < own
    # смешанно: 4 магазина в группе со своей опорой, 4 — к a; разные n — шум с весами групп
    mixed = {**{c: rs for c, rs in _stores(means[:4]).items()},
             **{200 + i: [m + d for d in (-3.0, -1.0, 1.0, 3.0, -2.0, 2.0)] for i, m in enumerate(means[4:])}}
    groups = {c: ('small' if c < 200 else f'chain:{c}') for c in mixed}
    m_own = [mean(mixed[c]) for c in mixed if c < 200]
    s2 = (sum(variance(mixed[c]) * (len(mixed[c]) - 1) for c in mixed)
          / sum(len(rs) - 1 for rs in mixed.values()))
    between = (sum((x - mean(m_own)) ** 2 for x in m_own) + sum(mean(mixed[c]) ** 2 for c in mixed if c >= 200)) / (3 + 4)
    noise = ((4 - 1) / 4 * sum(1 / len(mixed[c]) for c in mixed if c < 200)
             + sum(1 / len(mixed[c]) for c in mixed if c >= 200)) / (3 + 4)
    assert lr.shrink_k(mixed, groups) == pytest.approx(max(1.0, min(20.0, s2 / (between - s2 * noise))), abs=0.006)


def test_shrink_k_recovers_true_ratio():
    """Синтетика: σ = 6, τ = 3 → k = 36 / 9 = 4; 200 магазинов по 2–20 визитов."""
    rnd = random.Random(5)
    res, groups = {}, {}
    for c in range(200):
        g = ('small', 'medium', 'large', 'chain:017')[c % 4]
        centre = {'small': 6, 'medium': 10, 'large': 15, 'chain:017': 25}[g] + rnd.gauss(0, 3)
        res[c] = [centre + rnd.gauss(0, 6) for _ in range(rnd.randint(2, 20))]
        groups[c] = g
    assert lr.shrink_k(res, groups) == pytest.approx(4.0, rel=0.35)


# ============================== опора ==============================

def test_shrink_entries_prior_group_median_fallback_and_bounds():
    """Опора — медиана времени по факту магазинов группы с ≥ 2 визитами (их ≥ SHRINK_GROUP_MIN), иначе a; один визит —
    в записи есть (n = 1), но в опору группы не входит."""
    a = 5.0
    res = {1: [1.0, 3.0], 2: [5.0, 5.0, 5.0], 3: [10.0, 12.0], 4: [70.0],       # small: факты 7, 10, 16; 4 — 1 визит
           5: [20.0, 20.0], 6: [30.0, 30.0],                                   # medium: двух мало — опора a
           7: [40.0, 40.0], 8: [41.0, 41.0], 9: [42.0, 42.0],                  # chain — своя опора 46
           10: [-300.0, -300.0]}                                               # факт прижат к нижнему пределу
    groups = {1: 'small', 2: 'small', 3: 'small', 4: 'small', 5: 'medium', 6: 'medium', 7: 'chain:017',
              8: 'chain:017', 9: 'chain:017', 10: 'large'}
    e = lr.shrink_entries(res, a, groups)
    assert e['1'] == [2, 7.0, 10.0] and e['2'] == [3, 10.0, 10.0] and e['4'] == [1, 75.0, 10.0]
    assert e['5'] == [2, 25.0, 5.0] and e['6'] == [2, 35.0, 5.0]
    assert e['7'][2] == e['8'][2] == e['9'][2] == 46.0
    assert e['10'] == [2, lr.STORE_FACT_BOUNDS[0], 5.0]                          # large: один магазин — a
    neg = lr.shrink_entries({c: [-60.0, -60.0] for c in range(3)}, 5.0, {c: 'small' for c in range(3)})
    assert all(v[2] == 0.0 for v in neg.values())                                # опора не меньше 0


def _sim(seed=1, per_group=40, sigma=6.0, tau=3.0, b=12.0, holdout=2):
    """GPS-визиты с известным временем магазинов: группы малый / средний / крупный / сеть (крупная по весу), у
    магазина — 1–20 визитов в обучении и holdout на отложенной неделе; шум σ, разброс магазинов в группе τ."""
    rnd = random.Random(seed)
    train_from, test_from = lr.windows(TODAY)
    train_days = [train_from + timedelta(days=i) for i in range((test_from - train_from).days)]
    test_days = [test_from + timedelta(days=i) for i in range(lr.HOLDOUT_DAYS)]
    groups = {'small': (6.0, 0.04), 'medium': (10.0, 0.15), 'large': (15.0, 0.5), 'chain': (24.0, 0.6)}
    obs, truth, chains, visits, tons = [], {}, {}, {}, {}
    cid = 1000
    for g, (mu, t) in groups.items():
        for _ in range(per_group):
            cid += 1
            truth[cid], tons[cid] = mu + rnd.gauss(0, tau), t
            if g == 'chain':
                chains[cid] = '017'
            visits[cid] = rnd.choice([1, 2, 2, 3, 3, 4, 4, 5, 6, 8, 10, 12, 15, 20])
            for d in rnd.sample(train_days, visits[cid]) + rnd.sample(test_days, holdout):
                tonnes = t * rnd.uniform(0.7, 1.3)
                obs.append(lr.UnloadObs(d, 1, tonnes, max(1.0, truth[cid] + b * tonnes + rnd.gauss(0, sigma)), (cid,)))
    return obs, truth, chains, visits, tons


def _cur(o):
    return 20.0 * o.n + 6.0 * o.tonnes        # действующая норма: отсечение 3 × — не мешает сетям (≈ 31 мин)


def test_fit_unload_chains_are_own_group_not_large(monkeypatch):
    """Сети (chain_groups) — своя группа: опора сетей — их медиана (≈ 24 + груз), а не крупных магазинов; без chains
    те же магазины попадают в крупные по весу и получают общую опору."""
    monkeypatch.setattr(lr, 'SHRINK_MIN_TEST', (10 ** 6, 3))      # сглаживание действует и остаётся — записи в строке
    obs, truth, chains, _, _ = _sim()
    p = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains).params
    prior = {int(c): v[2] for c, v in p['store_shrink'].items()}
    chain_p = {prior[c] for c in chains}
    large_p = {prior[c] for c in truth if 1081 <= c <= 1120}
    assert len(chain_p) == 1 and len(large_p) == 1 and min(chain_p) - max(large_p) > 5
    lumped = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON).params
    lp = {int(c): v[2] for c, v in lumped['store_shrink'].items()}
    assert {lp[c] for c in chains} == {lp[c] for c in truth if 1081 <= c <= 1120}
    # размер — по среднему весу визита: у малых, средних и крупных — своя опора (время по факту — без груза b·т)
    small_p, medium_p = {prior[c] for c in truth if c <= 1040}, {prior[c] for c in truth if 1041 <= c <= 1080}
    assert len(small_p) == len(medium_p) == 1 and len(small_p | medium_p | large_p) == 3


# ============================== применение ==============================

def test_shrink_times_formula_manual_then_group():
    """t = (n·факт + k·опора группы) / (n + k). Введённое (ревью H1, как №60) — само, пока визитов меньше STORE_MIN_OBS;
    с STORE_MIN_OBS визитов — сглаживание к группе, введённое не участвует; без введённого — и с одного визита."""
    row = _row()
    t = lr.shrink_times(6.0, {}, row)
    assert t[101] == (pytest.approx((1 * 30 + 4 * 10) / 5 - 6, abs=0.051), 'shrink')            # 14 − 6 = 8
    assert t[102] == (pytest.approx((3 * 20 + 4 * 10) / 7 - 6, abs=0.051), 'shrink')
    assert t[103] == (pytest.approx((12 * 40 + 4 * 10) / 16 - 6, abs=0.051), 'shrink')
    m = lr.shrink_times(6.0, {101: 50.0, 102: 90.0, 104: 25.0}, row)
    assert m[101] == (44.0, 'manual')                                    # 1 визит < STORE_MIN_OBS — введённое само
    assert m[102] == t[102]                                              # 3 визита — к группе, введённое 90 не тянет
    assert m[104] == (19.0, 'manual') and 105 not in m                   # без визитов
    assert lr.store_times(6.0, {101: 50.0, 102: 90.0, 104: 25.0}, row) == m          # store_times — то же
    assert lr.store_extras(6.0, {}, row) == {c: e for c, (e, _) in t.items()}
    # поправка < 0,5 — ноль; время не меньше 0
    assert lr.shrink_times(6.0, {}, _row(shrink={'1': [5, 6.2, 6.2]}))[1] == (0.0, 'shrink')
    assert lr.shrink_times(6.0, {}, _row(shrink={'1': [5, -20.0, 0.0]}))[1] == (-6.0, 'shrink')
    # unload_extra — по точке: среднее магазинов точки, как у №60
    extra = lr.unload_extra(6.0, {}, row, {101: (40.0, 44.0), 102: (40.0, 44.0), 103: (40.1, 44.1)})
    assert extra[(40.0, 44.0)] == pytest.approx((t[101][0] + t[102][0]) / 2)


def test_store_times_n60_unless_shrink_chosen_and_valid():
    """Без выбора сглаживания (rule n60, строки до №66, битая запись) — ровно №60: store_times как без новых полей."""
    row = _row()
    plain = {k: v for k, v in row.items() if k not in ('store_shrink', 'store_rule')}
    n60 = lr.store_times(6.0, {101: 50.0}, plain)
    assert lr.store_rule(plain) == 'n60' and lr.store_rule(row) == 'shrink'
    for bad in ({'rule': 'n60', 'k': 4.0}, {'rule': 'shrink', 'k': 0.5}, {'rule': 'shrink', 'k': 21.0},
                {'rule': 'shrink', 'k': '4'}, {'rule': 'shrink', 'k': True}, {'rule': 'shrink'}, {'k': 4.0}, 'shrink',
                ['shrink', 4.0], None, {'rule': 'shrink', 'k': float('nan')}):
        broken = {**row, 'store_rule': bad}
        assert lr.store_rule(broken) == 'n60' and lr.store_times(6.0, {101: 50.0}, broken) == n60, bad
        assert lr.valid_params('unload', broken)                       # запись правила строку не портит
    no_entries = {k: v for k, v in row.items() if k != 'store_shrink'}
    assert lr.store_rule(no_entries) == 'n60'
    # битая запись store_shrink — только у своего магазина: введённое или норма
    bad_entries = {'101': [0, 30.0, 10.0], '102': [3, 20.0, -1.0], '103': [2.0, 5.0, 5.0], 'x': [2, 5.0, 5.0],
                   '104': [2, 91.0, 5.0], '105': [2, 5.0, 121.0], '106': [True, 5.0, 5.0], '107': [2, 5.0],
                   '108': [2, 20.0, 10.0]}
    got = lr.store_times(6.0, {101: 50.0}, _row(shrink=bad_entries))
    assert got == {101: (44.0, 'manual'), 108: (pytest.approx((2 * 20 + 4 * 10) / 6 - 6, abs=0.051), 'shrink')}
    assert lr.store_shrink({**row, 'store_shrink': 'oops'}) == {} and lr.store_shrink(None) == {}


def test_fit_unload_without_enough_stores_is_as_before():
    """Магазинов мало для k (6 < SHRINK_K_MIN) — сглаживание не участвует: строка и причина — как до №66."""
    out = lr.fit_unload(_unload_obs(store_extra={101: 10.0}), lambda o: 8 * o.n + 6 * o.tonnes, TODAY)
    assert set(out.params) == {'per_stop_min', 'per_tonne_min', 'store_offsets', 'store_stats'}
    assert 'խանութի ժամանակը' not in out.reason and lr.store_rule(out.params) == 'n60'


# ============================== выбор проверкой ==============================

def test_holdout_gate_picks_shrink_and_keeps_it():
    obs, _, chains, _, _ = _sim()
    out = lr.fit_unload(obs, _cur, TODAY, chains=chains)
    r = out.params['store_rule']
    assert r['rule'] == 'shrink' and lr.store_rule(out.params) == 'shrink'
    assert r['mae']['shrink'] <= r['mae']['n60'] * (1 - lr.MIN_GAIN) and r['n_test'] >= lr.SHRINK_MIN_TEST[0]
    assert lr.SHRINK_K_BOUNDS[0] <= r['k'] <= lr.SHRINK_K_BOUNDS[1] and r['days_test'] == lr.HOLDOUT_DAYS
    assert out.reason.startswith('ընդունված է') and 'կանոնն ավելի ճշգրիտ է' in out.reason
    # проверка строки — ровно то, что применится: store_extras по выбранному правилу
    a, b = out.params['per_stop_min'], out.params['per_tonne_min']
    ex = lr.store_extras(a, {}, out.params)
    _, test = lr._capped(obs, TODAY, _cur, lr.UNLOAD_CAP_REL)
    after = lr._mae((a * o.n + b * o.tonnes + sum(ex.get(c, 0.0) for c in o.customers), o.minutes) for o in test)
    assert out.mae_after == pytest.approx(after, abs=0.002)
    # уже действует — остаётся (обратно на №60 — только если тот точнее на MIN_GAIN)
    again = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains).params
    assert again['store_rule']['rule'] == 'shrink' and again['store_rule']['mae'] == r['mae']
    assert again['store_shrink'] == out.params['store_shrink'] and 'k_carried' not in again['store_rule']


def test_holdout_compares_only_visits_to_stores_seen_in_training():
    """Ревью L4: в сравнении правил — только визиты проверки к магазинам с визитами в обучении (у остальных прогнозы
    обоих правил одинаковы — разбавляли бы выигрыш); визиты новых магазинов n_test и ошибки не меняют."""
    obs, _, chains, _, _ = _sim()
    test_from = lr.windows(TODAY)[1]
    fresh = [lr.UnloadObs(test_from + timedelta(days=i % 7), 1, 0.1, 9.0 + i % 5, (5000 + i,)) for i in range(200)]
    base = lr.fit_unload(obs, _cur, TODAY, chains=chains)
    more = lr.fit_unload(obs + fresh, _cur, TODAY, chains=chains)
    assert more.n_test == base.n_test + len(fresh)
    r0, r1 = base.params['store_rule'], more.params['store_rule']
    assert (r1['n_test'], r1['days_test'], r1['mae'], r1['rule']) == (r0['n_test'], r0['days_test'], r0['mae'], r0['rule'])
    seen = {c for o in obs if o.day < test_from for c in o.customers}
    assert r0['n_test'] == sum(1 for o in obs if o.day >= test_from and set(o.customers) & seen)


def test_shrink_in_effect_keeps_k_when_it_cannot_be_estimated():
    """Ревью M3: сглаживание действует, а этой ночью k не оценить (мало магазинов) — правило не слетает молча: k —
    действующей строки, факты свежие, сменить правило может только проверка. Без действующего сглаживания — как до №66."""
    obs, _, chains, _, _ = _sim()
    obs = [o for o in obs if o.customers[0] <= 1007]                   # 7 магазинов < SHRINK_K_MIN[0]
    out = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains)
    r = out.params['store_rule']
    assert r['k'] == 4.0 and r['k_carried'] is True and r['rule'] == 'shrink' and out.params['store_shrink']
    assert r['k_day'] == SHRINK_ON['store_rule']['k_day']               # день оценки не обновляется при переносе
    assert 'գործող տողից' in out.reason and lr.store_rule(out.params) == 'shrink'
    plain = lr.fit_unload(obs, _cur, TODAY, chains=chains)
    assert 'store_rule' not in plain.params and 'store_shrink' not in plain.params


def _aged(days):
    return {**SHRINK_ON, 'store_rule': {**SHRINK_ON['store_rule'], 'k_day': (TODAY - timedelta(days=days)).isoformat()}}


def test_carried_k_expires_after_max_age_through_acceptance_gate(monkeypatch):
    """Ревью L-new-4: k переносится не дольше SHRINK_K_MAX_AGE дней от оценки. Устарел (или день оценки неизвестен) —
    сглаживание не участвует: строка по №60 с причиной (k_expired), и она заменит действующую строку со сглаживанием
    только через общее правило принятия строки; не принята — действует прежняя строка со сглаживанием (не молча №60)."""
    obs, _, chains, _, _ = _sim()
    obs = [o for o in obs if o.customers[0] <= 1007]                   # k этой ночью не оценить
    kept = lr.fit_unload(obs, _cur, TODAY, current_unload=_aged(lr.SHRINK_K_MAX_AGE), chains=chains)
    assert kept.params['store_rule']['rule'] == 'shrink' and kept.params['store_rule']['k_carried'] is True
    for current in (_aged(lr.SHRINK_K_MAX_AGE + 1), {**SHRINK_ON, 'store_rule': {'rule': 'shrink', 'k': 4.0}},
                    {**SHRINK_ON, 'store_rule': {'rule': 'shrink', 'k': 4.0, 'k_day': 'вчера'}}):
        out = lr.fit_unload(obs, _cur, TODAY, current_unload=current, chains=chains)
        p = out.params
        assert p['store_rule'] == {'rule': 'n60', 'k_expired': current['store_rule'].get('k_day')}
        assert 'store_shrink' not in p and lr.store_rule(p) == 'n60'
        assert f'ավելի քան {lr.SHRINK_K_MAX_AGE} օր առաջ' in out.reason and 'այլապես մնում է գործող տողը' in out.reason
        assert lr.store_extras(p['per_stop_min'], {}, p) == lr.store_extras(
            p['per_stop_min'], {}, {k: v for k, v in p.items() if k != 'store_rule'})   # ровно №60
    # общее правило не приняло строку — действует прежняя строка со сглаживанием
    monkeypatch.setattr(lr, '_accept', lambda before, after, gains, groups='days': (False, 'չի ընդունվել', 0.0))
    old = {'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {}, **_aged(40)}
    out = lr.fit_unload(obs, _cur, TODAY, current_unload=old, chains=chains)
    rows = [{'kind': 'unload', 'scope': '', 'accepted': 1, 'params': old, 'model_id': None},
            {'kind': 'unload', 'scope': '', 'accepted': int(out.accepted), 'params': out.params, 'model_id': None}]
    assert not out.accepted and lr.store_rule(lr.in_effect(rows, {}, None).unload) == 'shrink'


def test_rejected_row_does_not_claim_rule_is_applied(monkeypatch):
    """Ревью L2: строка не принята — в причине сказано, что выбор правила не применяется."""
    obs, _, chains, _, _ = _sim()
    monkeypatch.setattr(lr, '_accept', lambda before, after, gains, groups='days': (False, 'չի ընդունվել', 0.0))
    out = lr.fit_unload(obs, _cur, TODAY, chains=chains)
    assert not out.accepted and 'չի կիրառվում՝ տողն ընդունված չէ' in out.reason


def test_store_shrink_written_only_when_shrink_chosen():
    """Ревью L6: записи store_shrink — только при выборе сглаживания (гистерезису они не нужны: каждую ночь — свежие);
    выбран №60 — в строке лишь store_rule (k и ошибки для страницы)."""
    obs, _, chains, _, _ = _sim(sigma=1.0, tau=12.0)
    p = lr.fit_unload(obs, _cur, TODAY, chains=chains).params
    assert p['store_rule']['rule'] == 'n60' and 'store_shrink' not in p


def test_holdout_gate_keeps_and_returns_to_n60_when_stores_differ_a_lot():
    """Магазины сильно различаются, шум мал (τ = 20, σ = 1): №60 точнее и устойчиво — сглаживание не включается, а
    включённое раньше возвращается к №60 тем же правилом."""
    obs, _, chains, _, _ = _sim(sigma=1.0, tau=20.0)
    keep = lr.fit_unload(obs, _cur, TODAY, chains=chains).params['store_rule']
    back = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains).params['store_rule']
    assert keep['mae']['n60'] <= keep['mae']['shrink'] * (1 - lr.MIN_GAIN)
    assert keep['rule'] == 'n60' and back['rule'] == 'n60'


def test_rule_switch_needs_robust_gain_like_truck_time():
    """Смена правила — по общему правилу принятия (_accept): №60 точнее на ≥ 2%, но не устойчиво по дням проверки
    (τ = 12, σ = 1) — действующее сглаживание остаётся, причина «ոչ հուսալի»."""
    obs, _, chains, _, _ = _sim(sigma=1.0, tau=12.0)
    out = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains)
    r = out.params['store_rule']
    assert r['mae']['n60'] <= r['mae']['shrink'] * (1 - lr.MIN_GAIN) and r['rule'] == 'shrink'
    assert 'ոչ հուսալի' in out.reason and 'մնում է «հարթեցում դեպի նման խանութները» կանոնը' in out.reason


def test_rule_switch_off_when_chain_groups_unknown():
    """Ревью L-new-2: группы сетей этой ночью не получены (rule_switch=False) — правило не меняется, причина сказана."""
    obs, _, chains, _, _ = _sim()
    on = lr.fit_unload(obs, _cur, TODAY, chains=chains)
    off = lr.fit_unload(obs, _cur, TODAY, chains=chains, rule_switch=False)
    assert on.params['store_rule']['rule'] == 'shrink' and off.params['store_rule']['rule'] == 'n60'
    assert off.params['store_rule']['mae'] == on.params['store_rule']['mae'] and 'store_shrink' not in off.params
    assert 'ERP-ից չստացվեցին' in off.reason
    back = lr.fit_unload(obs, _cur, TODAY, current_unload=SHRINK_ON, chains=chains, rule_switch=False)
    assert back.params['store_rule']['rule'] == 'shrink'


def test_hysteresis_needs_min_gain_and_enough_holdout(monkeypatch):
    """Гистерезис, как у truck_time: смена — только при выигрыше ≥ MIN_GAIN и достаточной проверке; иначе в обе стороны
    остаётся действующее правило."""
    obs, _, chains, _, _ = _sim()
    monkeypatch.setattr(lr, 'MIN_GAIN', 0.9)                  # выигрыш 90% недостижим
    for rule in lr.STORE_RULES:
        out = lr.fit_unload(obs, _cur, TODAY, current_unload=_current(rule), chains=chains)
        assert out.params['store_rule']['rule'] == rule and f'մնում է «{lr.STORE_RULE_TITLES[rule]}»' in out.reason
    monkeypatch.setattr(lr, 'MIN_GAIN', 0.02)
    monkeypatch.setattr(lr, 'SHRINK_MIN_TEST', (10 ** 6, 3))  # проверки мало
    for rule in lr.STORE_RULES:
        out = lr.fit_unload(obs, _cur, TODAY, current_unload=_current(rule), chains=chains)
        assert out.params['store_rule']['rule'] == rule and 'քիչ են' in out.reason
    monkeypatch.setattr(lr, 'SHRINK_MIN_TEST', (30, 10 ** 3))  # дней проверки мало
    assert lr.fit_unload(obs, _cur, TODAY, chains=chains).params['store_rule']['rule'] == 'n60'
    bogus = {'store_rule': {'rule': 'bogus', 'k': 4.0}, 'store_shrink': {}}
    assert lr.fit_unload(obs, _cur, TODAY, current_unload=bogus, chains=chains).params['store_rule']['rule'] == 'n60'


# ============================== синтетика: №60 против сглаживания ==============================

SIM_BUCKETS = ('1', '2', '3', '4', '5–6', '7–12', '13–20')


def _bucket(n):
    return str(n) if n <= 4 else '5–6' if n <= 6 else '7–12' if n <= 12 else '13–20'


def simulate_errors(seeds=range(1, 11), manual_share=0.0):
    """Ошибка прогноза визита (своё время + груз) против истинного ожидания, мин, по числу визитов в обучении: правило
    №60 и сглаживание — с одними a, b строки; доля прогонов, где проверка выбрала сглаживание. manual_share — доля
    магазинов-сетей с введённым логистом точным временем (ревью H1)."""
    sums = defaultdict(lambda: [0.0, 0.0, 0])
    picked = 0
    for seed in seeds:
        obs, truth, chains, visits, tons = _sim(seed)
        manual = {c: float(round(truth[c])) for c in sorted(chains)[:int(len(chains) * manual_share)]}
        picked += lr.fit_unload(obs, _cur, TODAY, manual, chains=chains).params['store_rule']['rule'] == 'shrink'
        with pytest.MonkeyPatch.context() as mp:         # те же a, b и записи — при любом выборе проверки
            mp.setattr(lr, 'SHRINK_MIN_TEST', (10 ** 6, 3))
            p = lr.fit_unload(obs, _cur, TODAY, manual, current_unload=SHRINK_ON, chains=chains).params
        a, b = p['per_stop_min'], p['per_tonne_min']
        n60 = lr.store_extras(a, manual, {**p, 'store_rule': {**p['store_rule'], 'rule': 'n60'}})
        shrink = lr.store_extras(a, manual, {**p, 'store_rule': {**p['store_rule'], 'rule': 'shrink'}})
        for c, t in truth.items():
            if manual_share and c not in manual:
                continue
            want = t + 12.0 * tons[c]
            s = sums[_bucket(visits[c])]
            s[0] += abs(a + n60.get(c, 0.0) + b * tons[c] - want)
            s[1] += abs(a + shrink.get(c, 0.0) + b * tons[c] - want)
            s[2] += 1
    return {k: (round(s[0] / s[2], 2), round(s[1] / s[2], 2), s[2]) for k, s in sums.items()}, picked / len(seeds)


def test_simulation_shrink_beats_n60_at_few_visits_and_converges():
    errors, picked = simulate_errors()
    for k in ('2', '3', '4'):
        n60, shrink, _ = errors[k]
        assert shrink <= 0.85 * n60, (k, errors[k])                       # при 2–4 визитах — заметно точнее
    n60, shrink, _ = errors['13–20']
    assert abs(n60 - shrink) <= 0.15 * n60, errors['13–20']                # при многих — сходятся
    gain = {k: errors[k][0] - errors[k][1] for k in SIM_BUCKETS}
    assert gain['13–20'] < min(gain['2'], gain['3'], gain['4'])
    assert picked >= 0.7          # проверка это видит (с устойчивостью _accept — 8 из 10 прогонов; 7 дней проверки)


def test_simulation_correct_manual_on_heavy_stores_not_worse_than_n60():
    """Ревью H1: логист вписал точное время всем сетям (тяжёлые магазины, груз b̂·т далёк от настоящего) — со
    сглаживанием их ошибка не хуже №60: введённое — само до 2-го визита (как №60), дальше — к группе, а не к введённому."""
    errors, picked = simulate_errors(range(1, 7), manual_share=1.0)
    n60 = sum(e[0] * e[2] for e in errors.values()) / sum(e[2] for e in errors.values())
    shrink = sum(e[1] * e[2] for e in errors.values()) / sum(e[2] for e in errors.values())
    assert shrink <= n60 * 1.02, errors
    assert errors['1'][0] == errors['1'][1]                               # 1 визит — введённое у обоих правил
    assert picked >= 0.5


# ============================== «Развоз»: байт-в-байт и применение ==============================

def _digest(client, params):
    _dispatch_setup(client, [_dorder(i + 1, cid, GOLDEN_KG[cid], agent=1 + i % 2, rev=20000.0 + 1000 * i)
                             for i, cid in enumerate(sorted(GOLDEN_POINTS))])
    state = client.application.extensions['route_optimizer']
    for cid, p in GOLDEN_POINTS.items():
        state.store.save_geo_override(cid, p, 'qa')
    if params is not None:
        state.store.save_learned('2026-09-01', [lr.Outcome('unload', '', True, 'да', params)])
    plan = _plan(_build(client, ('CAR1', 'CAR2')))
    assert plan.pop('advice', None) is None
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


GOLDEN_ROW = {'per_stop_min': 6.5, 'per_tonne_min': 9.0, 'store_offsets': {'201': 7.5, '205': -2.0, '212': 12.0}}
SHRINK = {'store_shrink': {'201': [3, 14.0, 9.0], '203': [1, 40.0, 9.0], '207': [2, 25.0, 9.0]}}


@pytest.mark.parametrize('extra', [{}, {**SHRINK, 'store_rule': {'rule': 'n60', 'k': 4.0, 'mae': {'n60': 1.0}}},
                                   {**SHRINK, 'store_rule': {'rule': 'shrink', 'k': 0.2}}])
def test_dispatch_byte_identical_without_shrink_choice(client, extra):
    """Нет выбора сглаживания (нет полей, выбран №60, битый k) — «Развоз» байт-в-байт как прежде (golden №50)."""
    assert _digest(client, {**GOLDEN_ROW, **extra}) == GOLDEN['learned']


def test_dispatch_no_learned_rows_byte_identical(client):
    assert _digest(client, None) == GOLDEN['plain']


def test_dispatch_applies_shrink_when_chosen(client):
    """Выбрано сглаживание — «Развоз» считает время магазинов по нему (другой план); у магазина — ровно shrink_times."""
    row = {**GOLDEN_ROW, **SHRINK, 'store_rule': {'rule': 'shrink', 'k': 4.0}}
    assert _digest(client, row) != GOLDEN['learned']
    state = client.application.extensions['route_optimizer']
    eff = lr.in_effect(state.store.learned(accepted_only=True), state.store.learning_auto(), None)
    assert lr.store_rule(eff.unload) == 'shrink'
    snap, _ = state.snapshots.cached()
    bundle = views._bundle(state)
    point = GOLDEN_POINTS[203]
    ctx = views._dispatch_ctx(state, snap, bundle, date(2026, 10, 4), views._ready_trucks(snap, bundle), [point],
                              {203: point})
    assert ctx.tn.unload_at(0.0, point) == pytest.approx((1 * 40 + 4 * 9) / 5, abs=0.051)   # 1 визит — уже в расчёте


# ============================== подсказки и страница ==============================

def test_api_hint_and_learning_page_follow_active_rule(client, monkeypatch):
    state = _learning_client(client, monkeypatch)
    state.store.save_customer_constraints(102, None, None, 'qa', 20)
    state.store.save_learned('2026-09-02', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {}, 'store_stats': {'102': [3, 30.0]},
        'store_shrink': {'101': [1, 41.0, 11.0], '102': [3, 30.0, 11.0]}, 'store_rule': {'rule': 'n60', 'k': 4.0}})])
    data = client.get('/api/routes/customer-vehicles?q=C10').get_json()
    rows = {c['customer_id']: c for c in data['customers']}
    assert data['unload_norms'] == {'per_stop_min': 6.0, 'per_tonne_min': 10.0}          # №60 — ответ как прежде
    assert (rows[101]['unload_visits'], rows[101]['unload_auto_min']) == (None, 6.0)
    assert (rows[102]['unload_visits'], rows[102]['unload_auto_min']) == (3, 30.0)
    st = next(s for s in client.get('/api/routes/learning/status').get_json()['status'] if s['kind'] == 'unload')['stores']
    assert (st['rule'], st['k']) == ('n60', None)
    # на следующий день выбрано сглаживание
    state.store.save_learned('2026-09-03', [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {}, 'store_stats': {'102': [3, 30.0]},
        'store_shrink': {'101': [1, 41.0, 11.0], '102': [3, 30.0, 11.0]}, 'store_rule': {'rule': 'shrink', 'k': 4.0}})])
    data = client.get('/api/routes/customer-vehicles?q=C10').get_json()
    rows = {c['customer_id']: c for c in data['customers']}
    assert data['unload_norms'] == {'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_rule': 'shrink'}
    assert (rows[101]['unload_visits'], rows[101]['unload_auto_min']) == (1, round((41 + 4 * 11) / 5, 1))   # 1-й визит
    assert (rows[102]['unload_visits'], rows[102]['unload_auto_min']) == (3, round((3 * 30 + 4 * 11) / 7, 1))
    st = next(s for s in client.get('/api/routes/learning/status').get_json()['status'] if s['kind'] == 'unload')['stores']
    by = {r['customer_id']: r for r in st['rows']}
    assert (st['rule'], st['k']) == ('shrink', 4.0)
    assert (by[101]['visits'], by[101]['source'], by[101]['in_calc_min']) == (1, 'shrink', round((41 + 4 * 11) / 5, 1))
    assert (by[102]['visits'], by[102]['source']) == (3, 'shrink')
    assert by[102]['in_calc_min'] == pytest.approx((3 * 30 + 4 * 11) / 7, abs=0.051)       # введённое 20 не участвует
    # введённое при 1 визите (ревью H1, L4): в расчёте — введённое, в подсказке визит виден (со сглаживанием — с 1-го)
    state.store.save_customer_constraints(101, None, None, 'qa', 15)
    hint = {c['customer_id']: c for c in client.get('/api/routes/customer-vehicles?q=C10').get_json()['customers']}
    assert (hint[101]['unload_visits'], hint[101]['unload_auto_min']) == (1, round((41 + 4 * 11) / 5, 1))
    by = {r['customer_id']: r for r in next(s for s in client.get('/api/routes/learning/status').get_json()['status']
                                            if s['kind'] == 'unload')['stores']['rows']}
    assert (by[101]['source'], by[101]['in_calc_min']) == ('manual', 15.0)


@pytest.mark.skipif(shutil.which('node') is None, reason='нет node')
def test_settings_hint_describes_active_rule():
    item = {'unload_auto_min': 12.0, 'unload_visits': 1}
    shrink, = _js_hints({'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_rule': 'shrink'}, [item])
    n60, = _js_hints({'per_stop_min': 6.0, 'per_tonne_min': 10.0}, [{**item, 'unload_visits': None}])
    assert 'հարթեցնում է' in shrink and 'երրորդին' not in shrink and 'նման խանութների' in shrink
    assert 'ձեր գրած ժամանակը' in shrink and 'դեպի ձեր գրած' not in shrink              # H1: введённое — не опора
    assert 'երրորդին' in n60 and 'հարթեցնում է' not in n60
    nb = '\N{NO-BREAK SPACE}'   # между числом и словом — неразрывный пробел (как на «Развозе»)
    assert f'արդեն եղել է 1{nb}բեռնաթափում' in shrink and shrink.endswith(f'Դատարկ՝ 12{nb}րոպե (ըստ փաստի)։')


def test_dispatch_dialog_and_learning_page_describe_active_rule():
    js = (ROOT / 'static' / 'js' / 'routes_dispatch.js').read_text(encoding='utf-8')
    hint = re.search(r'    function unloadHint\(x, norms\) \{\n.*?\n    \}\n', js.replace('\r\n', '\n'), re.S).group(0)
    assert "norms.store_rule === 'shrink'" in hint and 'հարթեցնում է' in hint and 'երրորդին' in hint
    assert 'դեպի ձեր գրած' not in hint
    html = (ROOT / 'templates' / 'routes_learning.html').read_text(encoding='utf-8')
    for piece in ('id="lrStoresRule"', 'id="lrStoresShrink" hidden', 'id="lrStoresK"', 'id="lrRuleN60"',
                  'id="lrRuleShrink" hidden', 'նման խանութների'):
        assert piece in html, piece
    ljs = (ROOT / 'static' / 'js' / 'routes_learning.js').read_text(encoding='utf-8')
    for piece in ("st.rule === 'shrink'", "$('lrStoresShrink').hidden = !shrink", "$('lrRuleN60').hidden = shrink",
                  "shrink: 'GPS + նման խանութներ'", "unloads(shrink ? 1 : st.min_visits)",
                  "p.store_rule.rule === 'shrink'", "գործում է մուտքագրվածը"):
        assert piece in ljs, piece
    assert 'shrink_manual' not in ljs
    # заголовки правил в причинах журнала — те же слова, что на странице (ревью L3)
    for title in lr.STORE_RULE_TITLES.values():
        assert f'«{title}»' in ' '.join(html.split()), title
    assert "routes_dispatch.js') }}?v=77" in (ROOT / 'templates' / 'routes_dispatch.html').read_text(encoding='utf-8')


def test_run_learning_passes_active_rule_chains_and_size(client, monkeypatch):
    """Ночной прогон: действующее правило (гистерезис), сети — из снимка ERP по chain_groups, пороги размера — настройки."""
    state = _learning_client(client, monkeypatch)
    r = client.post('/api/routes/settings', json={'settings': {'chain_groups': ['036'], 'size_small_max_kg': 50,
                                                               'size_medium_max_kg': 300}})
    assert r.status_code == 200, r.get_json()
    seen = []
    real = lr.fit_unload
    monkeypatch.setattr(lr, 'fit_unload', lambda *args, **kw: seen.append(args[5:]) or real(*args, **kw))
    views.run_learning(state, TODAY)
    current, chains, size_kg = seen[-1]
    assert lr.store_rule(current) == 'n60' and size_kg == (50.0, 300.0) and chains and set(chains.values()) == {'036'}
    # прогон дня выбрал сглаживание (строка того же дня заменяет) — завтра от него и сравнение
    state.store.save_learned(TODAY.isoformat(), [lr.Outcome('unload', '', True, 'да', {
        'per_stop_min': 6.0, 'per_tonne_min': 10.0, 'store_offsets': {}, 'store_shrink': {'101': [2, 9.0, 8.0]},
        'store_rule': {'rule': 'shrink', 'k': 3.0}})])
    views.run_learning(state, TODAY + timedelta(days=1))
    assert lr.store_rule(seen[-1][0]) == 'shrink' and seen[-1][0]['store_rule']['k'] == 3.0


def test_run_learning_survives_erp_error_in_chain_lookup(client, monkeypatch):
    """Ревью L-new-2: ERP не отдала группы магазинов вне плана — прогон не срывается: все виды учатся, сети — только
    по снимку, правило времени магазина этой ночью не меняется (rule_switch=False)."""
    state = _learning_client(client, monkeypatch)
    assert client.post('/api/routes/settings', json={'settings': {'chain_groups': ['036']}}).status_code == 200
    snap, extra = state.snapshots.cached()
    trimmed = replace(snap, customers={c: v for c, v in snap.customers.items() if c != 104})   # 104 — вне плана
    monkeypatch.setattr(state.snapshots, 'cached', lambda *a, **k: (trimmed, extra))
    calls = []

    def broken(ids):
        calls.append(list(ids))
        raise erp.ErpError('нет связи')
    state.group_loader = broken
    seen = []
    real = lr.fit_unload
    monkeypatch.setattr(lr, 'fit_unload', lambda *args, **kw: seen.append((args[6], kw)) or real(*args, **kw))
    out = {o.kind for o in views.run_learning(state, TODAY)}
    assert calls == [[104]] and {'unload', 'loading', 'travel'} <= out
    chains, kw = seen[-1]
    assert kw == {'rule_switch': False} and 104 not in chains and set(chains.values()) <= {'036'}
    state.group_loader = lambda ids: {104: '036'}
    views.run_learning(state, TODAY)
    chains, kw = seen[-1]
    assert kw == {'rule_switch': True} and chains.get(104) == '036'


def test_chain_customers_for_stores_outside_managers_plan():
    """Ревью M1: сеть узнаётся и у магазинов вне плана менеджеров — одним запросом группы (ERP, только чтение) по
    недостающим клиентам; сети не заданы — запроса нет."""
    calls = []

    def loader(ids):
        calls.append(list(ids))
        return {555: '036', 556: '777', 999: '036'}
    snap = SimpleNamespace(customers={101: erp.Customer(101, 'C101', 'К', '036', None, False, None),
                                      102: erp.Customer(102, 'C102', 'К', '017', None, False, None)})
    state = SimpleNamespace(group_loader=loader)
    bundle = SimpleNamespace(settings={'chain_groups': ['036']})
    assert views._chain_customers(state, snap, bundle, {101, 102, 555, 556}) == {101: '036', 555: '036'}
    assert calls == [[555, 556]]
    assert views._chain_customers(state, snap, SimpleNamespace(settings={'chain_groups': []}), {555}) == {}
    assert views._chain_customers(SimpleNamespace(group_loader=None), snap, bundle, {101, 555}) == {101: '036'}
    assert len(calls) == 1

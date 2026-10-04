# -*- coding: utf-8 -*-
"""Честное принятие выученных норм (№61, после оценки обучения 04.10.2026).

- устойчивость выигрыша: поверх правила «ошибка меньше хотя бы на 2%» — парный бутстреп по дням проверки (у расхода —
  по интервалам заправок): новая норма точнее действующей не меньше чем в 90% повторных выборок; на двух днях выигрыш
  в 2–3% бывает и шумом. Детерминирован; у каждого вида, принимающего по ошибке на отложенной неделе, и у выбора модели
  времени грузовиков (поверх гистерезиса). Доля — в причине строки (по-армянски) и в столбце confidence журнала;
- причина строки truck_time — по правде: нет участков факта — «мало данных», а не «Valhalla недоступен»; Valhalla
  включён, но матрица грузовика для точек факта ещё считается — прогон её ждёт, не дождался — так и пишет.

Только синтетика, временные базы; ERP не читается.  Запуск из корня проекта:
python -m pytest tests/test_learning_bootstrap.py -q
"""
import random
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('numpy')

from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as rst  # noqa: E402
from route_optimizer import views  # noqa: E402
from test_learning_loop import BASE, REF, TODAY, _days, _legs, _unload_obs  # noqa: E402
from test_route_optimizer import client  # noqa: E402,F401
from test_route_valhalla import fake  # noqa: E402,F401
from test_truck_time_select import (MODEL, VALHALLA, FakeProvider, ValhallaFacts, _duel, _facts_client,  # noqa: E402
                                    bases)  # noqa: F401

TEST_FROM = TODAY - timedelta(days=lr.HOLDOUT_DAYS)


# ============================== бутстреп и правило принятия ==============================

def test_bootstrap_share_clear_gain_passes_noise_fails_deterministic():
    assert lr.bootstrap_share([0.4, 0.9, 0.2, 1.1, 0.3, 0.7, 0.5]) == 1.0          # каждый день лучше — всегда лучше
    noise = [2.0, -1.5]                                                              # два дня: один лучше, другой хуже
    share = lr.bootstrap_share(noise)
    assert share == pytest.approx(0.75, abs=0.03) and share < lr.BOOT_SHARE          # хуже — выборка «два плохих дня»
    assert lr.bootstrap_share(noise) == share and lr.bootstrap_share(list(noise)) == share   # детерминирован
    assert lr.bootstrap_share([0.0, 0.0]) == 0.0 and lr.bootstrap_share([]) == 0.0   # не лучше — не лучше
    assert lr.bootstrap_share([-1.0] * 5) == 0.0
    # 6 дней лучше, 1 день сильно хуже: выигрыш в среднем есть, но устойчив не в 90% выборок
    assert lr.bootstrap_share([1.0] * 6 + [-5.5]) < lr.BOOT_SHARE


def test_bootstrap_resamples_days_not_observations():
    """Ревью M10: группа бутстрепа — день проверки, а не наблюдение. День 1 — 30 наблюдений, новая норма каждое лучше на 1;
    день 2 — 40 наблюдений, каждое хуже на 0,5. По наблюдениям выигрыш выглядит устойчивым, по дням — нет (из выборок
    двух дней новая точнее только в 75%): норма не принимается."""
    rows = [(TEST_FROM, 30.0, 29.0, 20.0)] * 30 + [(TEST_FROM + timedelta(days=1), 30.0, 29.0, 29.75)] * 40
    assert lr._gains(rows) == [pytest.approx(30.0), pytest.approx(-20.0)]
    assert lr.bootstrap_share(lr._gains(rows)) == pytest.approx(0.75, abs=0.03)
    obs = [lr.LunchObs(TEST_FROM - timedelta(days=1 + i % 5), 29.0) for i in range(15)]
    obs += [lr.LunchObs(TEST_FROM, 20.0)] * 30 + [lr.LunchObs(TEST_FROM + timedelta(days=1), 29.75)] * 40
    o = lr.fit_lunch(obs, 30.0, TODAY, 30)
    assert o.params == {'minutes': 29.0} and o.mae_after <= 0.98 * o.mae_before          # выигрыш 2% есть
    assert not o.accepted and o.confidence == pytest.approx(0.75, abs=0.03) and o.reason.startswith('ոչ հուսալի')


def test_fuel_resamples_refuel_intervals():
    """Ревью M23: у расхода группа бутстрепа — интервал заправок. Из четырёх отложенных интервалов новая норма в одном
    лучше на 5 л/100 км, в трёх хуже на 1: в среднем лучше (ошибка 6,5 → 6), но устойчиво — нет (из выборок четырёх
    интервалов лучше только в ~68%)."""
    rnd = random.Random(8)
    days = [TEST_FROM - timedelta(days=30 - i) for i in range(12)] + [TEST_FROM + timedelta(days=i) for i in range(4)]
    obs = [lr.FuelObs(d, 0.1 + 0.05 * (i % 12), 25.0 + rnd.uniform(-0.01, 0.01)) for i, d in enumerate(days[:12])]
    obs += [lr.FuelObs(days[12], 0.4, 10.0)] + [lr.FuelObs(d, 0.4, 28.0) for d in days[13:]]
    o = lr.fit_fuel(obs, lambda u: 30.0, 'CAR1')
    assert o.params['empty_l100'] == pytest.approx(25.0, abs=0.1) and o.mae_after <= 0.98 * o.mae_before
    assert not o.accepted and o.confidence == pytest.approx(1 - 0.75 ** 4, abs=0.04), o.reason
    assert 'ստուգման միջակայքերի (լրիվ բաքերի միջև)' in o.reason


def test_accept_rule_needs_gain_and_robustness():
    ok, why, conf = lr._accept(2.0, 1.0, [1.5] * 7)
    assert ok and conf == 1.0 and why.startswith('ընդունված է․ սխալ 2 → 1․ հուսալի է՝')
    assert why.endswith('հուսալի է՝ ստուգման օրերի 2000 պատահական համադրությունից 100%-ում նոր նորմն ավելի ճշգրիտ է')
    # шум: в среднем на 2,5% лучше (20 наблюдений, ошибка 1,00 → 0,975), но один из двух дней хуже
    ok, why, conf = lr._accept(1.0, 0.975, [2.0, -1.5])
    assert not ok and conf < lr.BOOT_SHARE
    assert why.startswith('ոչ հուսալի․ սխալ 1 → 0,97, բայց միայն ստուգման օրերի 2000 պատահական համադրությունից')
    assert why.endswith('(պետք է առնվազն 90%)') and f'{lr._pct(conf)}%-ում' in why
    # выигрыш меньше 2% — прежняя причина (без доли), доля всё равно посчитана
    ok, why, conf = lr._accept(1.0, 0.99, [0.1] * 7)
    assert not ok and why.startswith('գործող նորմից առնվազն 2%-ով ավելի լավ չէ') and conf == 1.0
    assert lr._pct(0.8999) == '89,9' and lr._pct(0.9) == '90' and lr._pct(1.0) == '100'
    _, fuel, _ = lr._accept(2.0, 1.0, [0.5] * 4, 'intervals')
    assert 'ստուգման միջակայքերի (լրիվ բաքերի միջև) 2000' in fuel


def _loading_noise(second_day_bad):
    """Обучение: загрузка 10 + 8 мин/т (действующая норма 10 + 6). Проверка — два дня по 3,5 т: в первый день факт 60
    мин (новая норма ближе на 7 мин), во второй — 20 мин (ближе действующая) или тоже 60."""
    rnd = random.Random(2)
    obs = [lr.LoadObs(d, t, 10 + 8 * t + rnd.uniform(-1, 1)) for d in _days(33) for t in (1.0, 3.5)]
    obs += [lr.LoadObs(TEST_FROM, 3.5, 60.0) for _ in range(7)]
    obs += [lr.LoadObs(TEST_FROM + timedelta(days=1), 3.5, 20.0 if second_day_bad else 60.0) for _ in range(6)]
    return obs


def test_noise_on_two_days_rejected_real_gain_accepted_loading():
    noisy = lr.fit_loading(_loading_noise(True), TODAY, (10.0, 6.0))
    gain = 1 - noisy.mae_after / noisy.mae_before
    assert 0.02 <= gain <= 0.03, gain                                            # по одному правилу 2% — принята бы
    assert not noisy.accepted and noisy.reason.startswith('ոչ հուսալի') and noisy.confidence < lr.BOOT_SHARE
    assert noisy.params is not None                                              # выученное видно на странице
    real = lr.fit_loading(_loading_noise(False), TODAY, (10.0, 6.0))
    assert real.accepted and real.confidence == 1.0 and 'հուսալի է՝' in real.reason
    assert lr.fit_loading(_loading_noise(True), TODAY, (10.0, 6.0)) == noisy     # детерминирован


def _fuel_obs():
    rnd = random.Random(4)
    return [lr.FuelObs(d, u, 20 + 12 * u + rnd.uniform(-0.3, 0.3)) for d, u in zip(_days(24), [0.1, 0.6, 0.35, 0.8] * 6)]


def _all_kinds(bases_):
    """Каждый вид, принимающий по ошибке на отложенной неделе, на данных, где новая норма явно точнее."""
    return {
        'unload': lr.fit_unload(_unload_obs(), lambda x: 8 * x.n + 6 * x.tonnes, TODAY),
        'loading': lr.fit_loading(_loading_noise(False), TODAY, (10.0, 6.0)),
        'travel': lr.fit_travel(_legs(), TODAY, 'straight', REF, BASE),
        'fuel': lr.fit_fuel(_fuel_obs(), lambda u: 30.0, 'CAR1'),
        'truck_time': lr.fit_truck_time(_duel(VALHALLA, 0.05), TODAY, MODEL, bases_, {})[0],
    }


def test_every_kind_requires_robust_gain(bases, monkeypatch):
    clear = _all_kinds(bases)
    assert all(o.accepted and o.confidence >= lr.BOOT_SHARE for o in clear.values()), \
        {k: (o.accepted, o.confidence, o.reason) for k, o in clear.items()}
    assert clear['truck_time'].params['source'] == VALHALLA and '%-ում Valhalla-ն ավելի ճշգրիտ է' in clear['truck_time'].reason
    monkeypatch.setattr(lr, 'bootstrap_share', lambda gains, *a, **k: 0.5)      # выигрыш не устойчив
    shaky = _all_kinds(bases)
    for kind, o in shaky.items():
        assert not o.accepted and o.confidence == 0.5, kind
        assert 'ոչ հուսալի' in o.reason and '50%-ում' in o.reason, (kind, o.reason)
        assert o.mae_after == clear[kind].mae_after and o.mae_before == clear[kind].mae_before
    tt = shaky['truck_time']
    assert tt.params['source'] == MODEL and tt.reason.endswith('— մնում է նախկին մոդելը')   # гистерезис: остаётся


def test_confidence_stored_and_shown_in_status(client):
    state = client.application.extensions['route_optimizer']
    o = lr.fit_travel(_legs(), TODAY, 'straight', REF, BASE)
    state.store.save_learned('2026-10-02', [o, lr.Outcome('fuel', 'CAR1', False, 'мало данных')])
    rows = {r['kind']: r for r in state.store.learned()}
    assert rows['travel']['confidence'] == o.confidence == 1.0 and rows['fuel']['confidence'] is None
    st = {s['kind']: s for s in client.get('/api/routes/learning/status').get_json()['status']}
    assert st['travel']['last']['confidence'] == 1.0 and 'հուսալի է՝' in st['travel']['last']['reason']


LEARN_STEP = next(v for v, ddl in rst._MIGRATIONS.items() if any('learned_norms_v' in x and 'confidence' in x for x in ddl))


def test_learning_step_after_owner_steps():
    """Шаг журнала обучения (confidence, вид lunch) — после всех шагов, что уже в базе владельца (последний — առաքիչ,
    №62): иначе база на той схеме пропустила бы пересборку и журнал не читался бы без столбца confidence."""
    helper = next(v for v, ddl in rst._MIGRATIONS.items() if rst._TRUCK_HELPER_TABLE in ddl)
    assert LEARN_STEP > helper and rst.SCHEMA_VERSION >= LEARN_STEP + 1


def test_store_migrates_learning_step_keeps_rows_ids_and_allows_lunch(tmp_path):
    path = str(tmp_path / f'v{LEARN_STEP}.db')
    s = rst.Store(path)
    s.save_learned('2026-10-01', [lr.Outcome('truck_time', '', False, 'мало данных'),
                                  lr.Outcome('fuel', 'CAR1', False, 'мало данных', n_obs=3)])
    s.save_learning_auto('loading', True, 'qa')
    with closing(sqlite3.connect(path)) as conn:                                # база до шага: прежний журнал
        conn.execute('ALTER TABLE learned_norms RENAME TO learned_new')
        conn.execute(f'CREATE TABLE learned_norms({rst._LEARNED_COLUMNS_V14})')
        conn.execute(f'INSERT INTO learned_norms({rst._LEARNED_COPY}) SELECT {rst._LEARNED_COPY} FROM learned_new')
        conn.execute('DROP TABLE learned_new')
        conn.execute("UPDATE sqlite_sequence SET seq = 40 WHERE name = 'learned_norms'")
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(LEARN_STEP),))
        conn.commit()
        before = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO learned_norms(kind, scope, run_day, n_obs, n_test, accepted, reason, created_at) "
                         "VALUES('lunch', '', '2026-10-02', 0, 0, 0, 'x', 'now')")
    s2 = rst.Store(path)
    s2.save_learned('2026-10-02', [lr.Outcome('lunch', '', True, 'да', {'minutes': 25.0}, confidence=0.95)])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == \
            (str(rst.SCHEMA_VERSION),)
        rows = conn.execute('SELECT * FROM learned_norms ORDER BY id').fetchall()
        assert [r[:-1] for r in rows[:len(before)]] == before and all(r[-1] is None for r in rows[:len(before)])
        assert rows[-1][0] == 41 and rows[-1][1] == 'lunch' and rows[-1][-1] == 0.95
        assert conn.execute("SELECT name FROM sqlite_sequence WHERE name LIKE 'learned_norms%'").fetchall() == \
            [('learned_norms',)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'truck_helper'").fetchone()
    assert s2.learning_auto() == {'loading': True}
    assert [(r['kind'], r['confidence']) for r in s2.learned()] == [('fuel', None), ('truck_time', None), ('lunch', 0.95)]


# ============================== причина строки truck_time ==============================

def _fact(train_legs, test_legs):
    """Чистые участки факта прогона (LegObs прежней модели): train_legs за 10 дней обучения, test_legs за 5 дней
    проверки."""
    out = []
    for n, first, days in ((train_legs, TEST_FROM - timedelta(days=10), 10), (test_legs, TEST_FROM, 5)):
        out += [lr.LegObs(first + timedelta(days=k % days), True, False, 10, 10.0, 9.0, 9.0, 4.0, 25.0, 0, 600.0)
                for k in range(n)]
    return out


def test_truck_time_reason_tells_the_truth_without_valhalla(bases):
    no_valhalla = {MODEL: bases[MODEL]}
    for fact in ([], _fact(30, 10)):                                             # нет участков / мало
        o, fitted = lr.fit_truck_time([], TODAY, MODEL, no_valhalla, {}, fact=fact, preparing=True)
        assert not o.accepted and o.params is None and fitted == {}
        assert o.reason.startswith('քիչ տվյալներ․ ուսուցում՝ ') and o.reason.endswith('— մնում է նախկին մոդելը')
        assert (o.n_obs, o.n_test) == (len(fact) - min(len(fact), 10), min(len(fact), 10))
    enough = _fact(220, 70)
    wait, _ = lr.fit_truck_time([], TODAY, VALHALLA, no_valhalla, {}, fact=enough, preparing=True)
    assert wait.reason == ('Valhalla-ն միացված է, բայց փաստի կետերի համար բեռնատարի ժամանակները դեռ հաշվվում են․ '
                           'համեմատությունը կլինի հաջորդ վերահաշվարկին — մնում է Valhalla-ն')
    off, _ = lr.fit_truck_time([], TODAY, MODEL, no_valhalla, {}, fact=enough)
    assert off.reason.startswith('Valhalla-ն հասանելի չէ') and (off.n_obs, off.n_test) == (220, 70)
    old, _ = lr.fit_truck_time([], TODAY, MODEL, no_valhalla, {})                # участки неизвестны — как раньше
    assert old.reason.startswith('Valhalla-ն հասանելի չէ') and old.n_obs == 0
    assert lr.truck_time_short([d for o in enough for d in [o.day]], TODAY) is None
    assert lr.truck_time_short([], TODAY).startswith('քիչ տվյալներ')


class SlowProvider(FakeProvider):
    """Фон Valhalla ещё готовит матрицу: первые late запросов — None (как ValhallaProvider.get до готовности),
    preparing — пока не готово; ready=False — так и не готово."""

    def __init__(self, folder, late, ready=True):
        super().__init__(folder)
        self.late, self.ready, self.calls = late, ready, 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if not self.ready or self.calls <= self.late:
            return None
        return super().get(*args, **kwargs)

    def preparing(self):
        return not self.ready or self.calls <= self.late


def test_nightly_waits_for_valhalla_matrix_then_compares(client, fake, tmp_path, monkeypatch):
    """Ночь 04.10: прогон в 03:00 спросил матрицу грузовика, которую фон ещё не прочитал с диска, и сразу пошёл по графу
    OSM. Теперь прогон ждёт фон (пока тот работает, не дольше VALHALLA_WAIT_S) и сравнивает модели."""
    state = _facts_client(client, tmp_path, monkeypatch)
    monkeypatch.setattr(views, 'VALHALLA_POLL_S', 0.0)
    state.valhalla = SlowProvider(tmp_path / 'slow', late=3)
    out = {o.kind: o for o in views.run_learning(state, TODAY)}
    assert state.valhalla.calls > 3 and out['truck_time'].params is not None
    assert out['truck_time'].accepted and out['truck_time'].params['source'] == VALHALLA, out['truck_time'].reason


def test_nightly_reason_when_matrix_still_computing_or_data_short(client, fake, tmp_path, monkeypatch):
    state = _facts_client(client, tmp_path, monkeypatch)
    monkeypatch.setattr(views, 'VALHALLA_POLL_S', 0.0)
    monkeypatch.setattr(views, 'VALHALLA_WAIT_S', 0.0)                          # ждать некогда
    state.valhalla = SlowProvider(tmp_path / 'never', late=0, ready=False)
    tt = next(o for o in views.run_learning(state, TODAY) if o.kind == 'truck_time')
    assert not tt.accepted and tt.reason.startswith('Valhalla-ն միացված է') and tt.n_obs >= 200
    assert tt.reason.endswith('— մնում է նախկին մոդելը')
    # участков мало (три дня факта) — Valhalla не ждём, причина — мало данных
    state.fleet_facts = ValhallaFacts([TODAY - timedelta(days=i) for i in range(3, 0, -1)])
    monkeypatch.setattr(views, 'VALHALLA_WAIT_S', 3600.0)
    state.valhalla = SlowProvider(tmp_path / 'never2', late=0, ready=False)
    tt = next(o for o in views.run_learning(state, TODAY + timedelta(days=1)) if o.kind == 'truck_time')
    assert tt.reason.startswith('քիչ տվյալներ') and state.valhalla.calls == 1


def test_nightly_without_valhalla_and_without_legs_says_short(client, tmp_path, monkeypatch):
    state = _facts_client(client, tmp_path, monkeypatch)
    state.valhalla = None
    state.fleet_facts = ValhallaFacts([])                                       # трека нет — только заправки
    state.fleet_facts.refuels = lambda: [{'id': 'r1', 'car_code': 'CAR1', 'date': '2026-10-01',
                                          'at_utc': '2026-10-01T06:00:00+00:00',
                                          'payload': {'odometer_km': 1000, 'liters': 50, 'full_tank': True}}]
    tt = next(o for o in views.run_learning(state, TODAY) if o.kind == 'truck_time')
    assert tt.reason == 'քիչ տվյալներ․ ուսուցում՝ 0 / 200 (0 / 7 օր), ստուգում՝ 0 / 60 (0 / 3 օր) — մնում է ' \
                        'նախկին մոդելը'

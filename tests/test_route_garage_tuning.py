"""Журнал гаража — доработка после оценки (№53, «fix all»): растяжение крупного ремонта на 24/36 месяцев, сглаживание
цены к средней модели, средняя модели или парка машинам без своей цены (пустое ручное), плашка о пробеге; схема 18
(шаг 17 → 18 — после шага водителей 16 → 17, №62)."""
import ast
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import garage as gr  # noqa: E402
from route_optimizer import learning as lr  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_garage import AS_OF, CARS, DAY, NOW, TODAY, WS, _post, gclient  # noqa: E402,F401
from test_route_optimizer import _dispatch_setup, _dorder, _no_road_map, client, make_snapshot  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]


def E(day, kind, amount, km, spread=None):
    return gr.Entry(date.fromisoformat(day) if isinstance(day, str) else day, kind, amount, km, spread)


def share(amount, day, months, start, end):
    """Ожидаемая доля растянутого ремонта в (start, end] — независимо от garage.repair_share."""
    stop = gr.add_months(day, months)
    days = [day + timedelta(days=i) for i in range((stop - day).days)]
    return amount * sum(1 for d in days if start < d <= end) / len(days)


# ============================== растяжение крупного ремонта ==============================

@pytest.mark.parametrize('d,n,out', [(date(2026, 1, 31), 1, date(2026, 2, 28)), (date(2024, 2, 29), 24, date(2026, 2, 28)),
                                     (date(2026, 11, 30), 3, date(2027, 2, 28)), (date(2026, 12, 15), 1, date(2027, 1, 15)),
                                     (date(2026, 11, 15), 1, date(2026, 12, 15)), (date(2025, 4, 1), 36, date(2028, 4, 1))])
def test_add_months(d, n, out):
    assert gr.add_months(d, n) == out


def test_spread_repair_before_window_gives_its_overlapping_share():
    """Двигатель 2 400 000 ֏ за 18 месяцев до as_of, растянут на 24: в окно (start, end] идёт доля дней, попавших в окно,
    — и её одной хватает для «готово» (обычных ремонтов нет). Полная сумма — в итогах только если сделан в окне."""
    entries = [E('2025-04-01', 'repair', 2_400_000, 50_000, 24), E('2025-09-01', 'odometer', 0, 55_000),
               E('2026-09-30', 'odometer', 0, 85_000)]
    p = gr.price(entries, AS_OF)
    expected = share(2_400_000, date(2025, 4, 1), 24, WS, date(2026, 9, 30))
    assert (p.status, p.start, p.end) == ('ready', WS, date(2026, 9, 30))
    assert p.cost == pytest.approx(expected) and p.cost_amd == round(expected) and expected == pytest.approx(2_400_000 * 364 / 730)
    assert p.own == p.price == round(expected / p.km, 1) and p.repair_amd == 0          # сделан до окна
    old = gr.price([E('2024-04-01', 'repair', 2_400_000, 40_000, 24)] + entries[1:], AS_OF)   # 30 мес. назад — ≈ 6/24
    assert old.cost == pytest.approx(share(2_400_000, date(2024, 4, 1), 24, WS, date(2026, 9, 30))) and old.status == 'ready'
    assert old.cost / 2_400_000 == pytest.approx(6 / 24, abs=0.005)                     # 181 из 730 дней
    gone = gr.price([E('2023-06-01', 'repair', 2_400_000, 30_000, 24)] + entries[1:], AS_OF)  # кончился до окна
    assert (gone.cost, gone.status, gone.price) == (0.0, 'no_repairs', None)


def test_spread_ending_inside_window_and_n24_n36():
    base = [E('2025-09-01', 'odometer', 0, 55_000), E('2026-09-30', 'odometer', 0, 85_000)]
    end = date(2026, 9, 30)
    ends_inside = gr.price([E('2024-06-01', 'repair', 1_200_000, 40_000, 24)] + base, AS_OF)
    assert ends_inside.cost == pytest.approx(share(1_200_000, date(2024, 6, 1), 24, WS, end))
    assert ends_inside.cost == pytest.approx(1_200_000 * 242 / 730)                      # 02.10.2025 – 31.05.2026
    for n in (24, 36):
        p = gr.price([E('2026-01-01', 'repair', 900_000, 60_000, n)] + base, AS_OF)
        assert p.cost == pytest.approx(share(900_000, date(2026, 1, 1), n, WS, end))
    p24, p36 = (gr.price([E('2026-01-01', 'repair', 900_000, 60_000, n)] + base, AS_OF) for n in (24, 36))
    assert p24.cost == pytest.approx(900_000 * 273 / 730) and p36.cost == pytest.approx(900_000 * 273 / 1096)
    plain = gr.price([E('2026-01-01', 'repair', 900_000, 60_000)] + base, AS_OF)          # не растянут — вся сумма
    assert (plain.cost, plain.cost_amd, plain.repair_amd) == (900_000.0, 900_000, 900_000)
    assert p24.repair_amd == 900_000                                                    # итоги — полная сумма


def test_spread_on_first_reading_day_counts_from_next_day():
    """Растянутый ремонт в день первого показания (точка отсчёта): доля — с следующего дня; обычный — не входит."""
    entries = [E('2026-02-01', 'repair', 730_000, 10_000, 24), E('2026-09-01', 'odometer', 0, 20_000)]
    p = gr.price(entries, AS_OF)
    assert p.cost == pytest.approx(730_000 * (date(2026, 9, 1) - date(2026, 2, 1)).days / 730)
    assert gr.price([E('2026-02-01', 'repair', 730_000, 10_000)] + entries[1:], AS_OF).status == 'no_repairs'


def test_without_spread_and_single_ready_truck_price_unchanged():
    """Инвариант: без растянутых ремонтов и с одной готовой машиной — цена своя, как до доработки (30,0)."""
    entries = [E('2026-01-04', 'odometer', 0, 100_000), E('2026-03-10', 'repair', 200_000, 104_000),
               E('2026-06-15', 'repair', 400_000, 112_000), E('2026-09-28', 'odometer', 0, 120_000)]
    out = gr.prices({'CAR1': entries, 'CAR2': entries[:2]}, AS_OF, models={'CAR1': 'HOWO', 'CAR2': 'HOWO'})
    assert (out['CAR1'].price, out['CAR1'].own, out['CAR1'].blend, out['CAR2'].price) == (30.0, 30.0, None, None)


# ============================== сглаживание к средней модели ==============================

def _ready(cost, km, days=200):
    """Готовая цена машины: cost ֏ за km км (одним ремонтом в середине периода)."""
    start = date(2026, 1, 1)
    return [E(start, 'odometer', 0, 100_000), E(start + timedelta(days=days // 2), 'repair', cost, 100_000 + km // 2),
            E(start + timedelta(days=days), 'odometer', 0, 100_000 + km)]


def test_blend_tiers_model_fleet_none():
    """Две готовые HOWO — к средней HOWO (Σcost / Σkm, вместе с собой); JAC одна в модели — к средней парка (≥ 2 готовых);
    машина без модели — к средней парка; одна готовая машина в парке — без сглаживания."""
    journal = {'H1': _ready(600_000, 20_000), 'H2': _ready(200_000, 20_000), 'J1': _ready(100_000, 10_000),
               'X9': _ready(50_000, 5_000), 'N1': _ready(10_000, 300)}            # N1 — мало км: цены нет
    models = {'H1': 'HOWO', 'H2': 'HOWO', 'J1': 'JAC', 'N1': 'HOWO'}            # X9 — без модели
    out = gr.prices(journal, AS_OF, models=models)
    howo = 800_000 / 40_000
    fleet = 950_000 / 55_000
    assert (out['H1'].own, out['H1'].model_price, out['H1'].blend) == (30.0, 20.0, 'model')
    assert out['H1'].price == round((600_000 + 20_000 * howo) / (20_000 + 20_000), 1) == 25.0
    assert out['H2'].price == round((200_000 + 20_000 * howo) / 40_000, 1) == 15.0
    assert (out['J1'].blend, out['J1'].model_price) == ('fleet', round(fleet, 1))
    assert out['J1'].price == round((100_000 + 20_000 * fleet) / 30_000, 1)
    assert (out['X9'].blend, out['X9'].model) == ('fleet', None)
    assert (out['N1'].status, out['N1'].price, out['N1'].model) == ('low_km', None, 'HOWO')
    assert gr.effective(out) == {c: out[c].price for c in ('H1', 'H2', 'J1', 'X9')}
    alone = gr.prices({'H1': journal['H1'], 'N1': journal['N1']}, AS_OF, models=models)
    assert (alone['H1'].price, alone['H1'].blend, alone['H1'].model_price) == (30.0, None, None)


def test_blend_weight_k_and_more_km_more_own():
    """K = 20 000 км: машина с 20 000 км — посередине между своей и средней; с 5 000 км — ближе к средней, с 80 000 — к своей."""
    m = 10.0
    for km, expected in ((20_000, (30 + m) / 2), (5_000, (30 * 5_000 + m * 20_000) / 25_000),
                         (80_000, (30 * 80_000 + m * 20_000) / 100_000)):
        own = gr.price(_ready(30 * km, km), AS_OF)
        other = gr.price(_ready(round(m * 1_000_000), 1_000_000, days=250), AS_OF)
        avg = (30 * km + m * 1_000_000) / (km + 1_000_000)
        out = gr.blend({'A': own, 'B': other}, {'A': 'HOWO', 'B': 'HOWO'})
        assert other.price is not None and out['A'].blend == 'model'
        assert out['A'].price == round((30 * km + gr.BLEND_KM * avg) / (km + gr.BLEND_KM), 1)
        assert abs(out['A'].price - expected) < 0.35
    assert gr.BLEND_KM == 20_000


# ============================== средняя машинам без своей цены ==============================

def test_priors_tiers_model_fleet_none():
    """Машина без своей готовой цены (мало км, накапливается, записей журнала нет) — средняя модели, если в ней ≥ 2 готовых,
    иначе парка (≥ 2 готовых), иначе ничего; у готовых машин средней нет — у них своя (сглаженная)."""
    journal = {'H1': _ready(600_000, 20_000), 'H2': _ready(200_000, 20_000), 'J1': _ready(100_000, 10_000),
               'N1': _ready(10_000, 300),                       # HOWO, мало км
               'A1': _ready(50_000, 5_000, days=100)}            # JAC, накапливается
    models = {'H1': 'HOWO', 'H2': 'HOWO', 'J1': 'JAC', 'N1': 'HOWO', 'A1': 'JAC',
              'H3': 'HOWO', 'F1': 'FORD', 'X1': None}            # H3, F1, X1 — в журнале их нет
    found = gr.prices(journal, AS_OF, models=models)
    assert (found['N1'].status, found['A1'].status) == ('low_km', 'accumulating')
    howo, fleet = 800_000 / 40_000, 900_000 / 50_000
    assert gr.priors(found, models) == {
        'N1': gr.Prior(round(howo, 1), 'model', 'HOWO'), 'H3': gr.Prior(round(howo, 1), 'model', 'HOWO'),
        'A1': gr.Prior(round(fleet, 1), 'fleet', 'JAC'),        # готовая JAC одна — средняя парка
        'F1': gr.Prior(round(fleet, 1), 'fleet', 'FORD'), 'X1': gr.Prior(round(fleet, 1), 'fleet', None)}
    assert found['N1'].model_price is None and gr.effective(found) == {c: found[c].price for c in ('H1', 'H2', 'J1')}
    one = gr.prices({'H1': journal['H1'], 'N1': journal['N1']}, AS_OF, models=models)   # готовая одна на весь парк
    assert gr.priors(one, models) == {} and gr.priors({}, models) == {}
    jac = gr.prices({'J1': journal['J1'], 'H1': journal['H1'], 'A1': journal['A1']}, AS_OF, models=models)
    assert gr.priors(jac, {'J1': 'JAC', 'H1': 'HOWO', 'A1': 'JAC'}) == \
        {'A1': gr.Prior(round(700_000 / 30_000, 1), 'fleet', 'JAC')}


def test_bundle_prior_only_for_empty_manual_zero_is_set():
    """В расчёте: своя готовая цена журнала — всегда; иначе ручное «Износ, драм/км», если задано (и 0 — задано); пустое
    (None) — средняя модели или парка (garage_prior), wear_source garage_avg. Отпечаток без средних — прежний."""
    trucks = {'NUL': st.Truck('NUL', 5000.0, 18.0), 'ZERO': st.Truck('ZERO', 5000.0, 18.0, wear_amd_per_km=0.0),
              'MAN': st.Truck('MAN', 5000.0, 18.0, wear_amd_per_km=12.0), 'OWN': st.Truck('OWN', 5000.0, 18.0),
              'NONE': st.Truck('NONE', 5000.0, 18.0, wear_load_amd_per_km=3.0)}
    plain = st.Bundle(dict(st.DEFAULT_SETTINGS), None, trucks, {}, garage_wear={'OWN': 30.0})
    b = replace(plain, garage_prior={'NUL': 21.7, 'ZERO': 21.7, 'MAN': 21.7, 'OWN': 21.7, 'GONE': 9.0})
    r = b.resolved_trucks(set())
    assert {c: t.wear_amd_per_km for c, t in r.items()} == \
        {'NUL': 21.7, 'ZERO': 0.0, 'MAN': 12.0, 'OWN': 30.0, 'NONE': None}
    assert r['NONE'].wear_load_amd_per_km == 3.0 and 'GONE' not in r and b.trucks == trucks
    assert {c: b.wear_source(c) for c in (*trucks, 'GONE')} == {
        'NUL': 'garage_avg', 'ZERO': 'manual', 'MAN': 'manual', 'OWN': 'garage', 'NONE': None, 'GONE': 'garage_avg'}
    assert plain.wear_source('NUL') is None and plain.resolved_trucks(set())['NUL'].wear_amd_per_km is None
    assert plain.fingerprint() == replace(plain, garage_prior={}).fingerprint() != b.fingerprint()
    assert b.fingerprint() != replace(b, garage_prior={**b.garage_prior, 'NUL': 21.8}).fingerprint()


@pytest.mark.parametrize('name,capacity,model', [('HOWO SIN0TRUK', 5000.0, 'HOWO'), ('  jac 1040 ', 2200.0, 'JAC'),
                                                 (None, 2200.0, '2200 կգ'), ('', 2500.4, '2500 կգ'), (None, None, None)])
def test_model_of(name, capacity, model):
    assert gr.model_of(name, capacity) == model


# ============================== хранение: схема 18 ==============================

def _input(**kw):
    raw = {'car_code': 'CAR1', 'day': '2026-09-01', 'kind': 'repair', 'what': 'Շարժիչ', 'amount_amd': 2_400_000,
           'odometer_km': 100_000, **kw}
    return st.check_garage_entry({k: v for k, v in raw.items() if v is not ...}, CARS, TODAY)


@pytest.mark.parametrize('patch', [{'spread_months': 12}, {'spread_months': '24'}, {'spread_months': True},
                                   {'spread_months': 24, 'kind': 'accident'},
                                   {'spread_months': 36, 'kind': 'odometer', 'amount_amd': ..., 'what': ...}])
def test_check_spread_rejected(patch):
    item, errors = _input(**patch)
    assert item is None and 'spread_months' in errors and any('Ա' <= ch <= '֏' for ch in errors['spread_months'])


def test_check_spread_accepted():
    assert _input(spread_months=24)[0].spread_months == 24 and _input(spread_months=36.0)[0].spread_months == 36
    assert _input(spread_months=None)[0].spread_months is None and _input()[0].spread_months is None


def test_store_spread_roundtrip_history_and_db_check(tmp_path):
    s = st.Store(str(tmp_path / 'r.db'))
    a = s.save_garage_entry(_input(spread_months=24)[0], 'qa')
    assert [(e.id, e.spread_months) for e in s.garage_entries()] == [(a, 24)]
    assert s.save_garage_entry(_input(spread_months=36)[0], 'boss', a) == a
    hist = [e for e in s.garage_entries(deleted=True) if e.replaced_by == a]
    assert [e.spread_months for e in hist] == [24] and s.garage_entries()[0].spread_months == 36   # история переносит срок
    assert views._garage_by_car(s.garage_entries())['CAR1'][0].spread_months == 36
    with closing(sqlite3.connect(s.path)) as conn:
        for sql in ("INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at, spread_months) "
                    "VALUES('C', '2026-01-01', 'repair', 'x', 5, 1, 'x', 12)",
                    "INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at, spread_months) "
                    "VALUES('C', '2026-01-01', 'fixed', 'x', 5, 1, 'x', 24)"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
        conn.execute('PRAGMA ignore_check_constraints = ON')                       # битая строка мимо CHECK базы
        conn.execute("INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at, spread_months) "
                     "VALUES('C', '2026-01-01', 'accident', 'x', 5, 1, 'x', 24)")
        conn.commit()
    with pytest.raises(st.StoreError, match='բաշխում'):
        s.garage_entries()


GARAGE_STEP = next(v for v, ddl in st._MIGRATIONS.items() if any('garage_entry_v' in x for x in ddl))
V16_ROWS = [(5, 'CAR1', '2026-03-01', 'repair', 'Կոճղակներ', 55_000, 100_100, None, 'x', 'qa', None, None, None),
            (9, 'CAR1', '2026-03-01', 'repair', 'Կոճղակներ', 50_000, 100_000, 'n', 'x', 'qa', 'y', 'boss', 5),
            (12, 'CAR1', '2026-06-01', 'odometer', None, 0, 110_000, None, 'x', 'qa', None, None, None)]


def _old_db(tmp_path, version, drop=()):
    """База прежней схемы version: журнал гаража — как в схемах 16 и 17 (без spread_months), с тремя строками (правка с
    историей replaced_by и показание пробега); drop — таблицы, которых в той схеме ещё не было."""
    path = str(tmp_path / f'v{version}.db')
    s = st.Store(path)
    s.save_customer_constraints(101, None, st.CustomerWindow('between', 600, 720), 'qa', 40)
    s.save_truck_driver('CAR1', '2026-10-01', 'Արամ', 'qa')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('DROP TABLE garage_entry')
        for table in set(drop) - {'garage_entry'}:
            conn.execute(f'DROP TABLE {table}')
        if 'garage_entry' not in drop:
            conn.execute(st._GARAGE_TABLE_V16)
            conn.execute(st._GARAGE_ONE_ODOMETER)
            conn.executemany('INSERT INTO garage_entry(id, car_code, day, kind, what, amount_amd, odometer_km, note, '
                             'created_at, created_by, deleted_at, deleted_by, replaced_by) '
                             'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', V16_ROWS)
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
        conn.commit()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t != 'meta'}
    return path, before


def _check_migrated(path, before):
    """После миграции: схема текущая, журнал — те же строки, номера и replaced_by, у всех «не растянут», столбец и его
    CHECK на месте; индекс «одно показание пробега в день» работает; временной таблицы нет; прочие таблицы как были."""
    s = st.Store(path)
    assert s.load().unload_min == {101: 40.0}
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
        after = conn.execute('SELECT * FROM garage_entry ORDER BY 1').fetchall()
        assert [r[:-1] for r in after] == before.get('garage_entry', []) and {r[-1] for r in after} <= {None}
        assert {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before if t != 'garage_entry'} == \
            {t: v for t, v in before.items() if t != 'garage_entry'}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'garage_one_odometer'").fetchone()
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'garage_entry_v%'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):                                    # CHECK срока — новой схемы
            conn.execute("INSERT INTO garage_entry(car_code, day, kind, what, amount_amd, odometer_km, created_at, "
                         "spread_months) VALUES('C', '2026-01-01', 'repair', 'x', 5, 1, 'x', 12)")
    if before.get('garage_entry'):
        assert [(e.id, e.replaced_by, e.spread_months) for e in s.garage_entries(deleted=True)] == \
            [(5, None, None), (9, 5, None), (12, None, None)]
        with closing(sqlite3.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):   # индекс «одно в день»
            conn.execute("INSERT INTO garage_entry(car_code, day, kind, amount_amd, odometer_km, created_at) "
                         "VALUES('CAR1', '2026-06-01', 'odometer', 0, 110_500, 'x')")
    assert s.truck_drivers('2026-10-02')[0] == ({'CAR1': 'Արամ'} if 'truck_driver' in before else {})


def test_garage_step_is_17_to_18_after_truck_driver():
    """Шаг журнала (spread_months) — после шага водителей (№62, уже в базе владельца): 16 → 17 — truck_driver, 17 → 18 —
    журнал; иначе база на 17 пропустила бы пересборку и журнал остался бы без spread_months."""
    driver = next(v for v, ddl in st._MIGRATIONS.items() if st._TRUCK_DRIVER_TABLE in ddl)
    assert (driver, GARAGE_STEP) == (16, 17) and st.SCHEMA_VERSION >= GARAGE_STEP + 1   # дальше — свои шаги (19 — առաքիչ)


def test_store_migrates_17_to_18_keeps_rows_ids_and_history(tmp_path):
    """17 → 18 (как база владельца — водители уже есть): журнал пересобирается со столбцом spread_months."""
    _check_migrated(*_old_db(tmp_path, GARAGE_STEP))


def test_store_migrates_16_to_18_through_truck_driver(tmp_path):
    """16 → 17 → 18: сначала таблица водителей, затем пересборка журнала — строки журнала и история на месте."""
    _check_migrated(*_old_db(tmp_path, GARAGE_STEP - 1, drop=('truck_driver',)))


def test_store_migrates_14_to_18_chain(tmp_path):
    """14 → 15 → 16 → 17 → 18 (база, не видевшая ни времени у магазина, ни журнала, ни водителей): всё создаётся."""
    path, before = _old_db(tmp_path, 14, drop=('customer_unload', 'garage_entry', 'truck_driver'))
    s = st.Store(path)
    s.save_customer_unload(101, 40, 'qa')
    _check_migrated(path, before)


OWNER_DB = ROOT / 'route_optimizer.db'


@pytest.mark.skipif(not OWNER_DB.exists(), reason='нет базы маршрутов владельца')
def test_owner_db_copy_migrates_to_current(tmp_path):
    """Копия базы владельца (только чтение исходника; сейчас — схема 17 с водителями): все таблицы и строки те же, журнал
    — те же строки и «не растянут», схема — текущая (у журнала обучения — столбец confidence, строки те же)."""
    copy = tmp_path / 'owner.db'
    with closing(sqlite3.connect(f'file:{OWNER_DB.as_posix()}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(str(copy))) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(str(copy))) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        before = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in tables if t not in ('meta', 'garage_entry')}
        rows = conn.execute('SELECT * FROM garage_entry ORDER BY 1').fetchall() if 'garage_entry' in tables else []
    st.Store(str(copy)).load()
    with closing(sqlite3.connect(str(copy))) as conn:
        after = {t: conn.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in before}
        if before.get('learned_norms'):                    # шаг журнала обучения (№61) добавил столбец confidence (NULL)
            assert {r[-1] for r in after['learned_norms']} <= {None}
            after['learned_norms'] = [r[:len(before['learned_norms'][0])] for r in after['learned_norms']]
        assert after == before
        assert [r[:len(rows[0])] for r in conn.execute('SELECT * FROM garage_entry ORDER BY 1')] == rows if rows else True
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(st.SCHEMA_VERSION),)
        assert 'spread_months' in [r[1] for r in conn.execute('PRAGMA table_info(garage_entry)')]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'truck_driver'").fetchone()


# ============================== в расчёте и на странице ==============================

def _journal(store, car, cost, km=20_000, spread=None):
    base = 100_000
    for row in (st.GarageInput(car, date(2026, 1, 4), 'odometer', None, 0, base),
                st.GarageInput(car, date(2026, 6, 15), 'repair', 'Վերանորոգում', cost, base + km // 2, None, spread),
                st.GarageInput(car, date(2026, 9, 28), 'odometer', None, 0, base + km)):
        store.save_garage_entry(row, 'qa')


def test_models_from_snapshot_names_and_capacity_fallback(client, monkeypatch):
    """Модель — по имени, как его показывает раздел: ERP из снимка в памяти (ERP при этом не читается), у ручной машины —
    её название; снимка нет — тоннаж (имена ERP неизвестны)."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    assert client.post('/api/routes/settings', json={'manual_trucks': [
        {'car_code': 'M1', 'name': 'howo 2', 'capacity_kg': 5000, 'fuel_l_per_100km': 20, 'active': True}]}).status_code == 200
    trucks = state.store.load().trucks
    state.snapshots.cached()
    assert views._garage_models(state, trucks) == {'CAR1': 'HOWO', 'CAR2': 'FORD', 'M1': 'HOWO'}
    with client.application.test_request_context():   # в запросе запоминаются — по снимку и по машинам
        assert views._garage_models(state, trucks)['M1'] == 'HOWO'
        renamed = {**trucks, 'M1': replace(trucks['M1'], name='JAC 1'),
                   'CAR2': replace(trucks['CAR2'], capacity_kg=2200.0)}
        assert views._garage_models(state, renamed) == {'CAR1': 'HOWO', 'CAR2': 'FORD', 'M1': 'JAC'}
        assert views._garage_models(state, {'M1': trucks['M1']}) == {'M1': 'HOWO'}
    calls = []
    state.snapshots = SnapshotCache(lambda: calls.append(1) or make_snapshot())
    assert views._garage_models(state, trucks) == {'CAR1': '10000 կգ', 'CAR2': '3500 կգ', 'M1': 'HOWO'} and calls == []


def test_dispatch_uses_blended_price_model_tier(client, monkeypatch):
    """«Развоз»: CAR1 и ручная M1 «HOWO …» — обе готовы: в расчёте — сглаженные к средней HOWO; CAR2 (FORD) без журнала:
    ручное пусто — средняя парка, задано (и 0) — ручное; с журналом CAR2 (единственная в модели) — к средней парка."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    _dispatch_setup(client, [_dorder(1, 101, 400.0)])
    state = client.application.extensions['route_optimizer']
    assert client.post('/api/routes/settings', json={'manual_trucks': [
        {'car_code': 'M1', 'name': 'HOWO 2', 'capacity_kg': 5000, 'fuel_l_per_100km': 20, 'active': True}]}).status_code == 200
    _journal(state.store, 'CAR1', 600_000)
    _journal(state.store, 'M1', 200_000)
    trucks = {t['car_code']: t for t in client.get(f'/api/routes/dispatch?date={DAY}').get_json()['trucks']}
    assert (trucks['CAR1']['wear_amd_per_km'], trucks['M1']['wear_amd_per_km']) == (25.0, 15.0)
    assert (trucks['CAR2']['wear_amd_per_km'], trucks['CAR2']['wear_source']) == (20.0, 'garage_avg')   # 800 000 / 40 000
    for manual, expected in ((0, (0.0, 'manual')), (7.5, (7.5, 'manual')), (None, (20.0, 'garage_avg'))):
        assert client.post('/api/routes/settings', json={'trucks': [{'car_code': 'CAR2', 'wear_amd_per_km': manual}]}
                           ).status_code == 200
        assert state.store.load().trucks['CAR2'].wear_amd_per_km == (None if manual is None else float(manual))
        trucks = {t['car_code']: t for t in client.get(f'/api/routes/dispatch?date={DAY}').get_json()['trucks']}
        assert (trucks['CAR2']['wear_amd_per_km'], trucks['CAR2']['wear_source']) == expected
    _journal(state.store, 'CAR2', 100_000, km=10_000)
    state.snapshots = SnapshotCache(lambda: make_snapshot())   # снимка в памяти нет: «Развоз» берёт его до цены журнала
    trucks = {t['car_code']: t for t in client.get(f'/api/routes/dispatch?date={DAY}').get_json()['trucks']}
    fleet = 900_000 / 50_000
    assert trucks['CAR2']['wear_amd_per_km'] == round((100_000 + 20_000 * fleet) / 30_000, 1)
    assert (trucks['CAR1']['wear_amd_per_km'], trucks['M1']['wear_amd_per_km']) == (25.0, 15.0)   # модель — та же


def test_api_garage_summary_and_settings_show_own_model_used(gclient):
    c = gclient
    state = c.application.extensions['route_optimizer']
    assert c.get('/api/routes/garage').get_json()['journal_empty'] is True
    _journal(state.store, 'CAR1', 600_000)
    r = _post(c, {'car_code': 'CAR2', 'day': '2026-01-10', 'kind': 'repair', 'what': 'Շարժիչ', 'amount_amd': 2_400_000,
                  'odometer_km': 50_000, 'spread_months': 36})
    assert r.status_code == 200, r.get_json()
    for day, km in (('2026-05-01', 55_000), ('2026-09-30', 60_000)):
        assert _post(c, {'car_code': 'CAR2', 'day': day, 'kind': 'odometer', 'odometer_km': km}).status_code == 200
    g = c.get('/api/routes/garage').get_json()
    rows = {x['car_code']: x['garage'] for x in g['summary']}
    car2 = rows['CAR2']
    assert (car2['repair_amd'], car2['status']) == (2_400_000, 'ready') and g['journal_empty'] is False
    expected = share(2_400_000, date(2026, 1, 10), 36, date(2026, 1, 10), date(2026, 9, 30))
    assert car2['cost_amd'] == round(expected) and car2['own'] == round(expected / 10_000, 1)
    assert (rows['CAR1']['own'], rows['CAR1']['blend'], car2['blend']) == (30.0, 'fleet', 'fleet')
    m = (600_000 + expected) / 30_000
    assert rows['CAR1']['model_price'] == round(m, 1) and rows['CAR1']['price'] == round((600_000 + 20_000 * m) / 40_000, 1)
    entry = next(e for e in c.get('/api/routes/garage/entries?car=CAR2').get_json()['entries'] if e['kind'] == 'repair')
    assert entry['spread_months'] == 36
    settings = {t['car_code']: t for t in c.get('/api/routes/settings').get_json()['trucks']}
    assert settings['CAR1']['garage']['own'] == 30.0 and settings['CAR1']['garage']['price'] == rows['CAR1']['price']
    assert settings['CAR1']['wear_source'] == 'garage' and settings['CAR1']['garage_prior'] is None


def test_api_summary_and_settings_show_model_average_for_trucks_without_own(gclient):
    """Ручная M1 «HOWO» без журнала, ручное пусто: в «Ամփոփում» и «Настройках» в расчёте — средняя по журналу (CAR1 и
    CAR2 готовы, модели разные — средняя парка); ручное задано (0) — в расчёте ручное, средняя только видна."""
    c = gclient
    state = c.application.extensions['route_optimizer']
    m1 = {'car_code': 'M1', 'name': 'HOWO 2', 'capacity_kg': 5000, 'fuel_l_per_100km': 20, 'active': True}
    assert c.post('/api/routes/settings', json={'manual_trucks': [m1]}).status_code == 200
    row = {x['car_code']: x for x in c.get('/api/routes/garage').get_json()['summary']}['M1']
    assert (row['wear_source'], row['garage_prior'], row['garage']) == (None, None, None)          # журнал пуст
    _journal(state.store, 'CAR1', 600_000)
    _journal(state.store, 'CAR2', 200_000)
    prior = {'price': 20.0, 'scope': 'fleet', 'model': 'HOWO'}
    row = {x['car_code']: x for x in c.get('/api/routes/garage').get_json()['summary']}['M1']
    assert (row['wear_source'], row['garage_prior'], row['in_calc']) == ('garage_avg', prior, False)
    settings = {t['car_code']: t for t in c.get('/api/routes/settings').get_json()['trucks']}
    assert (settings['M1']['wear_source'], settings['M1']['garage_prior'], settings['M1']['wear_amd_per_km']) == \
        ('garage_avg', prior, None)
    assert settings['CAR1']['wear_source'] == 'garage' and settings['CAR1']['garage_prior'] is None
    assert c.post('/api/routes/settings', json={'manual_trucks': [{**m1, 'wear_amd_per_km': 0}]}).status_code == 200
    row = {x['car_code']: x for x in c.get('/api/routes/garage').get_json()['summary']}['M1']
    assert (row['wear_source'], row['garage_prior']) == ('manual', prior)


# ============================== замечания повторной проверки ==============================

def test_store_does_not_import_learning():
    """Предел прироста одометра — общий (garage.KM_PER_DAY_MAX): store не тянет learning (обучение «Развоза»)."""
    tree = ast.parse((ROOT / 'route_optimizer' / 'store.py').read_text(encoding='utf-8'))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert 'learning' not in imported and 'garage' in imported
    assert st.GARAGE_KM_PER_DAY == lr.REFUEL_KM_PER_DAY == gr.KM_PER_DAY_MAX == 1500.0


def test_garage_page_js_pins_401_badinput_spread_and_banner():
    """Страница: 401 — на вход с возвратом на журнал; нечисло в пробеге — ошибка строки (ничего не записано); поле
    «Բաշխել» только у ремонта с подсказкой от 300 000 ֏; плашка о пробеге старше 30 дней и подсказка пустого журнала."""
    js = (ROOT / 'static' / 'js' / 'routes_garage.js').read_text(encoding='utf-8')
    assert "if (resp.status === 401) {" in js
    assert "window.location.assign('/login?next=' + encodeURIComponent('/routes/garage'));" in js
    odo = js[js.index('async function saveOdo'):js.index('// ---------- итоги')]
    assert "inp.validity && inp.validity.badInput" in odo and "Ուղղեք նշված տողերը՝ ոչինչ չի պահպանվել։" in odo
    assert odo.index('validity.badInput') < odo.index("api('/api/routes/garage/odometers'")
    assert "body.spread_months = kind === 'repair' && $('gjSpread').value ? Number($('gjSpread').value) : null;" in js
    assert "spread_suggest_amd" in js and "d.rules.stale_days" in js and "d.journal_empty" in js
    settings = (ROOT / 'static' / 'js' / 'routes_settings.js').read_text(encoding='utf-8')
    note = settings[settings.index('function garageNote'):settings.index('const LOAD_COSTS')]
    code = [ln.split('//')[0] for ln in note.splitlines() if not ln.strip().startswith('//')]
    assert not [ln for ln in code if any('Ѐ' <= ch <= 'ӿ' for ch in ln)], 'строка журнала — только по-армянски (№58)'
    assert "'Ավտոտնակի գրառումներ․ վերանորոգումներ գրանցված չեն — '" in note and "'-ի միջինը՝ '" in note
    assert "' · մաշվածքը՝ ըստ ավտոտնակի գրառումների'" in settings
    # ручное задано — средняя по журналу видна, но «не применяется» (в расчёте — ручное)
    assert "' (չի կիրառվում, քանի որ վերևում արժեք կա)'" in note
    assert "'հաշվարկում է վերևի դաշտի արժեքը' + unused" in note
    assert ("used === 'garage_avg' ? 'հաշվարկում է ' + priorText(t.garage_prior) + ', քանի որ վերևի դաշտը դատարկ է'"
            in note)
    assert "'մոդելի միջինը'" in note and "'ավտոպարկի միջինը'" in note and "used === 'garage' || used === 'garage_avg'" in note
    assert "t.wear_source === 'garage_avg' ? ' · մաշվածքը՝ '" in settings
    summary = js[js.index('function renderSummary'):js.index('// ---------- загрузка')]
    assert "r.wear_source === 'garage_avg' ? [h('b', { class: 'gj-price', text: fmt(r.garage_prior.price, 1) })" in summary
    assert "r.wear_source === 'manual' && r.garage_prior ? [" in summary
    assert "'չի կիրառվում, քանի որ կարգավորումներում արժեք կա'" in summary
    # плашка — по серверу (stale), без дат браузера; фраза заканчивается «։»
    stale = js[js.index('function staleTrucks'):js.index('function goStale')]
    assert "state.data.summary.filter(r => r.active && r.stale)" in stale and 'new Date' not in stale
    assert "stale.map(t => t.car_code).join(', ') + '։'" in stale
    assert "'մոդելի միջին'" in summary and "'ավտոպարկի միջին'" in summary
    # глоссарий раздела (docs/research/armenian-glossary.md): износ — «մաշվածք», не «մաշվածություն»; парк — «ավտոպարկ»
    garage_line = settings[settings.index('function garageNote'):settings.index("h('div', { class: 'rs-load-fields' }")]
    for text in (js, garage_line):
        assert 'մաշվածություն' not in text and "'պարկի" not in text and ' պարկի' not in text
        assert 'մատյան' not in text                    # журнал гаража — «ավտոտնակի գրառումներ»
    html = (ROOT / 'templates' / 'routes_garage.html').read_text(encoding='utf-8')
    assert 'id="gjSpread"' in html and 'id="gjBanner"' in html and "routes_garage.js') }}?v=8" in html
    assert "routes_garage.css') }}?v=7" in html
    css = (ROOT / 'static' / 'css' / 'routes_garage.css').read_text(encoding='utf-8')
    assert '.gj-table td.gj-num .gj-sub { white-space: normal; font-family: var(--rt-font); }' in css
    assert '.gj-table .rt-cell-name .n { white-space: nowrap; overflow-wrap: normal; }' in css

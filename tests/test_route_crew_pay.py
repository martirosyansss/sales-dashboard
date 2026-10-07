# -*- coding: utf-8 -*-
"""«Աշխատավարձ» (docs/research/09-crew-pay.md, формула владельца 07.10): расчёт crew_pay, параметры в route_optimizer.db,
API страницы с подменённым загрузчиком ERP (живой ERP не нужен), CSV и доступ только администратору."""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import ErpError, check_sql, SQL_CREW_PAY  # noqa: E402
from test_route_optimizer import REF, _no_road_map, client  # noqa: E402,F401

NOW = datetime(2026, 10, 7, 10, 0)
LINE, LINE19, LINE19B = 1, 2, 3                   # менеджеры (линии)
KORYUN, AGHVAN, HELPER = 11, 12, 13               # экспедиторы
AGENTS = {LINE: ('A001/4', 'Մենեջեր'), LINE19: ('A008/3', '19 լ'), LINE19B: ('A008/6', '19 լ բ'),
          KORYUN: ('B001/1', 'Իսկանդարյան Կորյուն'), AGHVAN: ('B002/1', 'Համբարձումյան Աղվան'),
          HELPER: ('B008/10', 'Օգնական')}


def inv(van, day, customer, kg=0.0, total=1.0, line=LINE):
    return cp.Invoice(van, date(2026, 9, day), customer, line, total, kg)


def data(*invoices):
    return cp.CrewData(tuple(invoices), AGENTS)


def row(result, agent_id):
    return next(r for r in result.rows if agent_id in r.agent_ids)


# ============================== расчёт ==============================

def test_formula_full_month_piece_above_minimum():
    """Կորյուն все 2 рабочих дня: 30 точек и 4 т; норма 12 × 2 = 24 точки выполнена — минимум полный, но сдельная больше."""
    p = cp.Params(fix=100_000, rate_point=175, rate_tonne=1200, minimum=10_000, norm_per_day=12, old_fix=100_000, old_pct=2)
    invoices = [inv(KORYUN, 1, c, kg=100, total=10_000) for c in range(15)] + \
               [inv(KORYUN, 2, c, kg=100, total=10_000) for c in range(15, 30)] + [inv(AGHVAN, 2, 99, kg=50)]
    res = cp.compute(data(*invoices), p)
    r = row(res, KORYUN)
    assert res.workdays == 2
    assert (r.days, r.points, r.tonnes, r.sales) == (2, 30, 3.0, 300_000)
    assert r.fix == 100_000 and r.piece == 175 * 30 + 1200 * 3 == 8_850
    assert r.minimum == 10_000 and r.pay == 108_850 and not r.min_applied
    assert r.old == 100_000 + 6_000 and r.diff == 2_850
    assert [(d.day.day, d.points, d.tonnes, d.piece) for d in r.by_day] == [(1, 15, 1.5, 4_425), (2, 15, 1.5, 4_425)]


def test_points_are_distinct_store_days():
    """Несколько накладных (и линий) одному магазину в день — одна точка; тот же магазин в другой день — ещё одна."""
    invoices = [inv(KORYUN, 1, 5, kg=10), inv(KORYUN, 1, 5, kg=20), inv(KORYUN, 1, 5, kg=30, line=99),
                inv(KORYUN, 2, 5, kg=40)]
    r = row(cp.compute(data(*invoices), cp.Params()), KORYUN)
    assert (r.days, r.points) == (2, 2) and r.tonnes == pytest.approx(0.1)
    assert [d.points for d in r.by_day] == [1, 1]


def test_proration_by_days_and_minimum_applied():
    """Աղվան работал 1 день из 3: фикс 1/3; норма 12 × 3 = 36, у него 6 точек — минимум 1/6, и он больше фикса + сдельной."""
    invoices = [inv(KORYUN, d, 100 + d) for d in (1, 2, 3)] + [inv(AGHVAN, 2, c) for c in range(6)]
    p = cp.Params(fix=30_000, rate_point=100, rate_tonne=0, minimum=300_000, norm_per_day=12, old_fix=30_000, old_pct=0)
    r = row(cp.compute(data(*invoices), p), AGHVAN)
    assert r.days == 1 and r.fix == 10_000 and r.piece == 600
    assert r.minimum == 50_000 and r.pay == 50_000 and r.min_applied and r.old == 10_000


def test_minimum_capped_at_full_and_tie_is_not_applied():
    p = cp.Params(fix=0, rate_point=1000, rate_tonne=0, minimum=12_000, norm_per_day=6, old_fix=0, old_pct=0)
    r = row(cp.compute(data(*[inv(KORYUN, 1, c) for c in range(12)]), p), KORYUN)   # 12 точек при норме 6 — не больше MIN
    assert r.minimum == 12_000 and r.piece == 12_000 and r.pay == 12_000 and not r.min_applied


def test_money_rounds_half_up_and_row_adds_up():
    assert [cp.money(x) for x in (0.5, 1.5, 2.5, 2.4999, -0.5)] == [1, 2, 3, 2, 0]
    p = cp.Params(fix=100_000, rate_point=175, rate_tonne=1200, minimum=0, norm_per_day=12, old_fix=100_000, old_pct=2)
    invoices = [inv(KORYUN, d, d) for d in (1, 2, 3)] + [inv(AGHVAN, 1, 7, kg=1.25, total=0.25)]
    r = row(cp.compute(data(*invoices), p), AGHVAN)
    assert r.fix == 33_333 and r.piece == 177 and r.pay == r.fix + r.piece      # 175 + 1,5 ֏ → 177 (половина вверх)
    assert r.old == 33_333 + 0 and isinstance(r.pay, int)


def test_exclusions_lines_people_self_delivery_and_unknown_codes():
    """Линия 19 л, люди-исключения и «менеджер везёт сам» не считаются — ни в людях, ни в рабочих днях D."""
    invoices = [inv(KORYUN, 1, 1), inv(KORYUN, 2, 2, line=LINE19), inv(KORYUN, 3, 3, line=LINE19B),
                inv(HELPER, 4, 4), inv(LINE, 5, 5, line=LINE), inv(AGHVAN, 6, 6)]
    p = cp.Params(excluded_lines=('a008/3', 'A008/6', 'X1'), excluded_people=('B008/10',))
    res = cp.compute(data(*invoices), p)
    assert res.workdays == 2 and [r.agent_ids for r in res.rows] == [(KORYUN,), (AGHVAN,)]   # по имени: Ի раньше Հ
    assert row(res, KORYUN).points == 1 and res.unknown_codes == ("X1",)
    assert cp.compute(data(*invoices), cp.Params(excluded_lines=(), excluded_people=())).workdays == 5


def test_no_data_is_empty_not_division_by_zero():
    assert cp.compute(data(), cp.Params(excluded_people=('B008/10',))) == cp.Result(0, (), ())
    only_19 = data(inv(KORYUN, 1, 1, line=LINE19))
    assert cp.compute(only_19, cp.Params()).rows == () and cp.compute(only_19, cp.Params()).workdays == 0
    assert cp.totals(()) == {'points': 0, 'tonnes': 0, 'sales': 0, 'fix': 0, 'piece': 0, 'pay': 0, 'old': 0, 'diff': 0}


def test_check_params():
    ok, errors = cp.check_params(cp.Params().json())
    assert ok == cp.Params() and errors == {}
    ok, _ = cp.check_params({**cp.Params().json(), 'excluded_lines': ' a008/3, A008/3 ,, b1 ', 'excluded_people': ''})
    assert ok.excluded_lines == ('A008/3', 'B1') and ok.excluded_people == ()
    for field, bad in (('norm_per_day', 0), ('fix', -1), ('rate_point', True), ('rate_tonne', float('nan')),
                       ('minimum', '250000'), ('old_pct', 101), ('fix', None), ('excluded_lines', 'A1; DROP'),
                       ('excluded_people', 5), ('excluded_lines', ','.join(f'A{i}' for i in range(51)))):
        params, errors = cp.check_params({**cp.Params().json(), field: bad})
        assert params is None and set(errors) == {field}, (field, bad)
    assert cp.check_params([1])[0] is None
    params, errors = cp.check_params({})
    assert params is None and len(errors) == 9


def test_sql_is_read_only():
    check_sql(SQL_CREW_PAY)                    # read-only guard пропускает
    assert SQL_CREW_PAY.count('WITH (NOLOCK)') == 3 and SQL_CREW_PAY.count('?') == 2


def test_one_person_who_switched_code_is_one_row():
    """Կարապետ сменил код посреди месяца (B004/19, потом B003/24): дни кодов не пересекаются — одна строка, коды через
    запятую, формула один раз; код только с нулевыми документами в строку не попадает."""
    agents = {**AGENTS, 21: ('B004/19', 'հակոբյան  կարապետ '), 22: ('B003/24', 'Հակոբյան Կարապետ'),
              23: ('B009/1', 'Հակոբյան Կարապետ')}                                   # имя — по основному коду
    invoices = [inv(21, 1, 5, kg=100), inv(21, 1, 6, kg=60), inv(22, 2, 5, kg=50), inv(22, 3, 6, kg=10),
                inv(22, 3, 7, kg=40), inv(23, 4, 8, kg=0, total=0), inv(KORYUN, 4, 1)]
    p = cp.Params(fix=80_000, rate_point=100, rate_tonne=1000, minimum=0, norm_per_day=1, old_fix=0, old_pct=0)
    res = cp.compute(cp.CrewData(tuple(invoices), agents), p)
    assert len(res.rows) == 2 and res.workdays == 4 and res.overlapping_codes == ()
    r = row(res, 21)
    assert r.agent_ids == (22, 21) and r.code == 'B003/24, B004/19' and r.name == 'Հակոբյան Կարապետ'
    assert (r.days, r.points, r.tonnes) == (3, 5, pytest.approx(0.26))
    assert r.fix == 60_000 and r.piece == 500 + 260
    assert [(d.day.day, d.points, round(d.tonnes, 3)) for d in r.by_day] == [(1, 2, 0.16), (2, 1, 0.05), (3, 2, 0.05)]


def test_namesakes_on_same_days_are_separate_rows_with_warning():
    """Два разных առաքիչ-тёзки работают в одни дни: не сливаем (каждому — свой фикс и минимум), предупреждаем."""
    agents = {**AGENTS, 21: ('B004/19', 'Գրիգորյան Արմեն'), 22: ('B003/24', 'Գրիգորյան  Արմեն')}
    invoices = [inv(21, 1, 5, kg=100), inv(21, 2, 6, kg=100), inv(22, 2, 7, kg=100), inv(22, 3, 8, kg=100)]
    p = cp.Params(fix=90_000, rate_point=0, rate_tonne=0, minimum=0, norm_per_day=1, old_fix=0, old_pct=0)
    res = cp.compute(cp.CrewData(tuple(invoices), agents), p)
    assert [r.agent_ids for r in res.rows] == [(22,), (21,)] and [r.fix for r in res.rows] == [60_000, 60_000]
    assert res.overlapping_codes == (('B003/24', 'B004/19'),)


def test_three_namesakes_one_overlap_stay_three_rows():
    """A и B работали в один день, C — в другие: группа не сливается целиком, три строки и одна группа в предупреждении."""
    agents = {**AGENTS, 21: ('B1', 'Գրիգորյան Արմեն'), 22: ('B2', 'Գրիգորյան Արմեն'), 23: ('B3', 'գրիգորյան արմեն')}
    invoices = [inv(21, 1, 1, kg=10), inv(22, 1, 2, kg=10), inv(23, 2, 3, kg=10)]
    res = cp.compute(cp.CrewData(tuple(invoices), agents), cp.Params())
    assert sorted(r.agent_ids for r in res.rows) == [(21,), (22,), (23,)]
    assert res.overlapping_codes == (('B1', 'B2', 'B3'),)


def test_csv_lists_what_to_check(pay):
    AGENTS.update({21: ('B1', 'Գրիգորյան Արմեն'), 22: ('=B2', 'Գրիգորյան Արմեն'), 23: ('B3', 'Օգնական')})
    pay.source['invoices'] = [inv(21, 1, 1, kg=10), inv(22, 1, 2, kg=10), inv(23, 2, 3, kg=10),
                              inv(HELPER, 2, 4, kg=10)]
    try:
        lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    finally:
        for k in (21, 22, 23):
            del AGENTS[k]
    checks = [x for x in lines if x.startswith('Ստուգել;')]
    assert len(checks) == 2 and '=B2, B1' in checks[0] and checks[0].count(';') == 1
    assert checks[1].endswith('չհաշվվողների մեջ՝ B3')
    assert lines.index(checks[-1]) < next(i for i, x in enumerate(lines) if x.startswith('Կոդ;'))


def test_excluded_person_with_another_counted_code_is_warned():
    agents = {**AGENTS, 21: ('B003/24', 'Օգնական')}                       # HELPER B008/10 исключён, его второй код — нет
    res = cp.compute(cp.CrewData((inv(HELPER, 1, 1), inv(21, 2, 2), inv(KORYUN, 3, 3)), agents), cp.Params())
    assert res.excluded_kin == ('B003/24',) and len(res.rows) == 2


def test_float_noise_is_not_a_point():
    invoices = [inv(KORYUN, 1, 1, kg=0.0004, total=0.4), inv(KORYUN, 2, 2, kg=0.001, total=0),
                inv(KORYUN, 3, 3, kg=0, total=0.6)]
    r = row(cp.compute(data(*invoices), cp.Params()), KORYUN)
    assert [d.day.day for d in r.by_day] == [2, 3]


def test_zero_and_negative_only_documents_make_no_point_or_day():
    """Клиенто-день из нулей или минусов (возврат) — не точка и не рабочий день; сумма без веса — точка."""
    invoices = [inv(KORYUN, 1, 1, kg=100, total=10), inv(KORYUN, 2, 2, kg=0, total=0),
                inv(KORYUN, 3, 3, kg=-20, total=-500), inv(KORYUN, 4, 4, kg=0, total=300),
                inv(KORYUN, 5, 5, kg=50, total=10), inv(KORYUN, 5, 5, kg=-30, total=-5, line=99)]
    res = cp.compute(data(*invoices), cp.Params())
    r = row(res, KORYUN)
    assert res.workdays == 3 and [d.day.day for d in r.by_day] == [1, 4, 5]
    assert r.points == 3 and r.tonnes == pytest.approx(0.12) and r.sales == 315
    assert cp.compute(data(inv(KORYUN, 1, 1, kg=0, total=0)), cp.Params()).workdays == 0


def test_duplicate_erp_code_excludes_every_id():
    agents = {**AGENTS, 31: ('A008/3', '19 լ կրկնօրինակ'), 32: ('B008/10 ', 'Օգնական 2')}
    invoices = [inv(KORYUN, 1, 1, line=31), inv(32, 2, 2), inv(KORYUN, 3, 3, line=LINE19), inv(AGHVAN, 4, 4)]
    res = cp.compute(cp.CrewData(tuple(invoices), agents), cp.Params())
    assert [r.agent_ids for r in res.rows] == [(AGHVAN,)] and res.workdays == 1


def test_default_excluded_people_match_research():
    assert cp.Params().excluded_people == ('B008/10', 'B008/3', 'B008/4', 'A008/6')
    agents = {**AGENTS, 41: ('A008/6', '19 լ առաքիչ')}
    res = cp.compute(cp.CrewData((inv(41, 1, 1, line=LINE), inv(KORYUN, 1, 2)), agents), cp.Params())
    assert [r.agent_ids for r in res.rows] == [(KORYUN,)]


def test_default_rates_owner_0710():
    p = cp.Params()
    assert (p.fix, p.rate_point, p.rate_tonne, p.minimum, p.norm_per_day, p.old_fix, p.old_pct) == \
        (100_000, 250, 1_750, 250_000, 12, 100_000, 2.0)


# Текущий месяц, 07.10: накладные по сегодня, а фикс и норма — от рабочих дней ВСЕГО месяца (D_month)
def oct_inv(van, day, customer, kg=0.0, total=1.0):
    return cp.Invoice(van, date(2026, 10, day), customer, LINE, total, kg)


def avakimyan_week():
    """Экран 07.10: 6 из 6 дней, 75 точек, 9,7 т (по 12–13 точек в день)."""
    out, c = [], 0
    for day, n in zip((1, 2, 3, 5, 6, 7), (13, 12, 13, 12, 13, 12)):
        for _ in range(n):
            c += 1
            out.append(oct_inv(KORYUN, day, c, kg=9_700 / 75))
    return out


def test_current_month_prorated_by_whole_month_not_elapsed_days():
    p = cp.Params()
    week = data(*avakimyan_week())
    before = row(cp.compute(week, p), KORYUN)                         # без D_month — как было: полный месяц за неделю
    assert (before.days, before.points, round(before.tonnes, 3)) == (6, 75, 9.7)
    assert before.fix == 100_000 and before.minimum == 250_000 and before.pay == 250_000
    res = cp.compute(week, p, 26)
    r = row(res, KORYUN)
    assert (res.workdays, res.workdays_month) == (6, 26)
    assert r.fix == cp.money(100_000 * 6 / 26) == 23_077
    assert r.piece == cp.money(250 * 75 + 1750 * 9.7) == 35_725
    assert r.minimum == cp.money(250_000 * 75 / (12 * 26)) == 60_096
    assert r.pay == 60_096 and r.min_applied
    assert r.old == 23_077 + cp.money(0.02 * 75)


def test_month_workdays_never_below_elapsed_days():
    """Календарь говорит «3 рабочих дня», а накладные были в 6 дней (работали в выходной) — D_month = D."""
    week, p = data(*avakimyan_week()), cp.Params()
    for planned in (3, 6, 0, None):
        res = cp.compute(week, p, planned)
        assert res.workdays_month == 6 and res.rows == cp.compute(week, p).rows, planned
    assert cp.compute(data(), p, 27).workdays_month == 27 and cp.compute(data(), p).workdays_month == 0


def save_calendar(pay, **settings):
    store = pay.application.extensions['route_optimizer'].store
    changes, errors = st.validate_payload({'settings': settings}, store.load(), REF)
    assert not errors, errors
    store.save(changes, 'qa')


def test_api_current_month_uses_calendar_of_routes_settings(pay):
    pay.source['invoices'] = avakimyan_week()
    body = pay.get('/api/routes/pay').get_json()
    assert body['current'] and (body['workdays'], body['workdays_month']) == (6, 27)      # пн–сб: 31 − 4 воскресенья
    k = body['rows'][0]
    assert k['fix'] == cp.money(100_000 * 6 / 27) and k['minimum'] == cp.money(250_000 * 75 / (12 * 27))
    # праздник в будний день и праздник в воскресенье (не вычитается дважды) — 26
    save_calendar(pay, holidays=['2026-10-12', '2026-10-04'])
    assert pay.get('/api/routes/pay').get_json()['workdays_month'] == 26
    # пятидневка: ещё 5 суббот (3, 10, 17, 24, 31) — 21
    save_calendar(pay, workdays=[1, 2, 3, 4, 5])
    body = pay.get('/api/routes/pay').get_json()
    assert body['workdays_month'] == 21 and body['rows'][0]['fix'] == cp.money(100_000 * 6 / 21)
    lines = pay.get('/api/routes/pay.csv').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'Աշխատանքային օրեր ամսում;21' in lines
    assert any(x.startswith('B001/1;Իսկանդարյան Կորյուն;6;6;75;') for x in lines)      # в таблице — D (6)
    assert len(pay.calls) == 1                                                            # календарь — без нового чтения ERP


def test_past_month_unchanged_and_saved_params_win(pay):
    """Регрессия: сентябрь со ставками 175 / 1 200 (сохранённая строка crew_pay сильнее новых значений по умолчанию) —
    те же числа, что до D_month, даже если календарь настроек сентябрю не соответствует."""
    old_rates = {**cp.Params().json(), 'rate_point': 175, 'rate_tonne': 1200}
    assert pay.post('/api/routes/pay/params', json=old_rates).status_code == 200
    save_calendar(pay, workdays=[1, 2, 3, 4, 5], holidays=['2026-09-01', '2026-09-21'])
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['params']['rate_point'] == 175 and body['params']['rate_tonne'] == 1200
    assert (body['workdays'], body['workdays_month']) == (2, 2)
    k, a = body['rows']
    assert (k['days'], k['points'], k['tonnes'], k['fix'], k['piece']) == (2, 2, 1.5, 100_000, 2_150)
    assert k['minimum'] == 20_833 and k['pay'] == 102_150 and k['old'] == 103_000 and k['diff'] == -850
    assert (a['days'], a['fix'], a['piece'], a['minimum'], a['pay'], a['old']) == (1, 50_000, 475, 10_417, 50_475, 50_400)
    lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'B001/1;Իսկանդարյան Կորյուն;2;2;2;1,500;100000;2150;20833;102150;;103000;-850' in lines
    assert 'Աշխատանքային օրեր ամսում;2' in lines


def test_huge_numbers_are_field_errors():
    for bad in (10 ** 400, -10 ** 400, float('inf'), float('-inf')):
        params, errors = cp.check_params({**cp.Params().json(), 'fix': bad, 'old_pct': bad})
        assert params is None and set(errors) == {'fix', 'old_pct'}, bad


# ============================== параметры в базе ==============================

def test_store_params_roundtrip_without_schema_change(tmp_path):
    store = st.Store(str(tmp_path / 'routes.db'))
    assert store.crew_pay_params() == (cp.Params(), None, None)
    p = cp.Params(fix=150_000, rate_point=220, rate_tonne=1500, excluded_people=())
    store.save_crew_pay_params(p, 'boss')
    params, at, by = store.crew_pay_params()
    assert params == p and by == 'boss' and at.startswith('20')
    assert st.CREW_PAY_KEY not in store.load().settings and st.SCHEMA_VERSION == 24   # настройки маршрутов не задеты


def test_store_corrupt_params_is_an_error(tmp_path):
    import sqlite3
    store = st.Store(str(tmp_path / 'routes.db'))
    store.save_crew_pay_params(cp.Params(), None)
    good = cp.Params().json()
    for bad in ('{"fix": -5}', 'not json', '[1]', json.dumps({**good, 'fix': 10 ** 400}),
                json.dumps(good).replace('"fix": 100000', '"fix": 1e400', 1)):
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE settings SET value = ? WHERE key = 'crew_pay'", (bad,))
        with pytest.raises(st.StoreError):
            store.crew_pay_params()


# ============================== API ==============================

SEPT = [inv(KORYUN, 1, 1, kg=1000, total=100_000), inv(KORYUN, 2, 2, kg=500, total=50_000),
        inv(AGHVAN, 2, 3, kg=250, total=20_000), inv(KORYUN, 3, 4, line=LINE19)]


@pytest.fixture
def pay(client, monkeypatch):
    """Клиент «Маршрутов», «сегодня» — 07.10.2026; роль — из g.user_role (как ставит app_v2), по умолчанию admin."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    app = client.application
    state = app.extensions['route_optimizer']
    calls = []
    source = {'invoices': SEPT, 'error': None}

    def loader(since, until):
        calls.append((since, until))
        if source['error']:
            raise source['error']
        return cp.CrewData(tuple(source['invoices']), AGENTS)
    state.crew_pay_loader = loader
    role = {'value': 'admin'}

    @app.before_request
    def _role():
        from flask import g
        if role['value']:
            g.user_role = role['value']
    client.role, client.calls, client.source = role, calls, source
    return client


def test_api_month(pay):
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['success'] and body['month'] == '2026-09' and not body['current'] and body['workdays'] == 2
    assert body['workdays_month'] == 2                                              # закрытый месяц: D_month = D
    assert pay.calls == [(date(2026, 9, 1), date(2026, 10, 1))]
    assert body['months'][0] == '2026-10' and body['months'][-1] == '2025-11' and len(body['months']) == 12
    assert [r['code'] for r in body['rows']] == ['B001/1', 'B002/1']
    k = body['rows'][0]
    assert (k['days'], k['points'], k['tonnes'], k['fix'], k['piece']) == (2, 2, 1.5, 100_000, 250 * 2 + 1750 * 1.5)
    assert k['minimum'] == 20_833 and k['pay'] == 103_125 and k['old'] == 103_000 and k['diff'] == 125
    assert [d['date'] for d in k['by_day']] == ['2026-09-01', '2026-09-02']
    assert body['totals']['pay'] == sum(r['pay'] for r in body['rows']) and body['params'] == cp.Params().json()
    pay.get('/api/routes/pay?month=2026-09')
    assert len(pay.calls) == 1                                                      # повтор — из кэша


def test_api_current_month_until_today_and_bad_months(pay):
    pay.source['invoices'] = []
    body = pay.get('/api/routes/pay').get_json()
    assert body['month'] == '2026-10' and body['current'] and body['workdays'] == 0 and body['rows'] == []
    assert body['workdays_month'] == 27                                     # октябрь 2026 без воскресений (пн–сб)
    assert pay.calls == [(date(2026, 10, 1), date(2026, 10, 8))]
    for bad in ('2025-10', '2026-11', '2026-13', 'x', '2026-9'):
        r = pay.get(f'/api/routes/pay?month={bad}')
        assert r.status_code == 400 and 'month' in r.get_json()['errors'], bad


def test_api_erp_down_is_an_error_not_zeros(pay):
    pay.source['error'] = ErpError('нет связи')
    r = pay.get('/api/routes/pay?month=2026-08')
    assert r.status_code == 503 and r.get_json()['success'] is False and 'rows' not in r.get_json()
    assert pay.get('/api/routes/pay.csv?month=2026-08').status_code == 503
    erp_calls = len(pay.calls)
    params = pay.get('/api/routes/pay/params')                              # параметры — без ERP
    assert params.status_code == 200 and params.get_json()['params'] == cp.Params().json()
    assert params.get_json()['store_error'] is False and params.get_json()['updated_at'] is None
    saved = pay.post('/api/routes/pay/params', json={**cp.Params().json(), 'fix': 120_000})
    assert saved.status_code == 200 and saved.get_json()['params']['fix'] == 120_000 and saved.get_json()['updated_at']
    assert len(pay.calls) == erp_calls


def test_corrupt_params_row_shows_defaults_and_save_fixes_it(pay):
    import sqlite3
    store = pay.application.extensions['route_optimizer'].store
    store.save_crew_pay_params(cp.Params(fix=1), 'qa')
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE settings SET value = 'oops' WHERE key = 'crew_pay'")
    body = pay.get('/api/routes/pay/params').get_json()
    assert body['store_error'] is True and body['params'] == cp.Params().json()
    assert pay.get('/api/routes/pay?month=2026-09').status_code == 500          # расчёт с битой записью — ошибка, не дефолты
    assert pay.post('/api/routes/pay/params', json=cp.Params().json()).get_json()['store_error'] is False
    assert pay.get('/api/routes/pay?month=2026-09').status_code == 200


def test_csv(pay):
    pay.source['invoices'] = SEPT + [cp.Invoice(99, date(2026, 9, 2), 7, LINE, 0, 10),
                                     cp.Invoice(98, date(2026, 9, 2), 8, LINE, 0, 10)]
    AGENTS[99] = ('B9', '=HYPERLINK("x")')
    AGENTS[98] = ('=B8', 'Ծածկագիր')                                            # код ERP с «=» — тоже экранируется
    try:
        r = pay.get('/api/routes/pay.csv?month=2026-09')
    finally:
        del AGENTS[99], AGENTS[98]
    assert r.status_code == 200 and r.mimetype == 'text/csv'
    assert 'crew-pay-2026-09.csv' in r.headers['Content-Disposition']
    text = r.get_data().decode('utf-8')
    assert text.startswith('\ufeffԱշխատավարձ;2026-09\r\nՀաշվված է;2026-10-07 10:00\r\n')
    lines = text[1:].strip().split('\r\n')
    # параметры, с которыми посчитано (они действуют и на прошлые месяцы)
    assert 'Ֆիքս ամսական, ֏;100000' in lines and 'Հին սխեմա՝ վաճառքի տոկոս, %;2' in lines
    assert 'Չհաշվվող առաքիչներ;B008/10, B008/3, B008/4, A008/6' in lines
    head = next(i for i, x in enumerate(lines) if x.startswith('Կոդ;Առաքիչ;Օրեր;'))
    assert lines[head - 1] == '' and 'Հին սխեմա (2%)' in lines[head]
    table = lines[head + 1:]
    assert len(table) == 5
    assert "'=HYPERLINK" in text and "\n'=B8;Ծածկագիր;" in text                    # формула Excel не исполнится
    assert 'B001/1;Իսկանդարյան Կորյուն;2;2;2;1,500;100000;3125;20833;103125;;103000;125' in table
    assert 'Աշխատանքային օրեր ամսում;2' in lines[:head]
    assert table[-1].startswith(';Ընդամենը;;2;')


def test_csv_shows_when_params_changed(pay):
    assert pay.post('/api/routes/pay/params', json={**cp.Params().json(), 'old_pct': 1.5}).status_code == 200
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['params_updated_at'] and body['params_updated_by'] is None
    lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    assert lines[2].startswith('Պարամետրերը փոխվել են;') and 'Հին սխեմա՝ վաճառքի տոկոս, %;1,5' in lines
    assert any(x.startswith('Կոդ;') and 'Հին սխեմա (1,5%)' in x for x in lines)


def test_params_save_and_validation(pay):
    bad = pay.post('/api/routes/pay/params', json={**cp.Params().json(), 'norm_per_day': 0, 'fix': -1})
    assert bad.status_code == 400 and set(bad.get_json()['errors']) == {'norm_per_day', 'fix'}
    assert pay.post('/api/routes/pay/params', data='x', content_type='text/plain').status_code == 415
    good = {**cp.Params().json(), 'fix': 150_000, 'excluded_lines': 'A008/3, A008/6', 'excluded_people': ''}
    r = pay.post('/api/routes/pay/params', json=good)
    assert r.status_code == 200 and r.get_json()['params']['excluded_people'] == []
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['params']['fix'] == 150_000 and body['rows'][0]['fix'] == 150_000


@pytest.mark.parametrize('role', [None, 'garage', 'warehouse', 'user'])
def test_only_admin(pay, role):
    pay.role['value'] = role
    for path in ('/routes/pay', '/api/routes/pay', '/api/routes/pay.csv', '/api/routes/pay/params'):
        assert pay.get(path).status_code == 403, path
    assert pay.post('/api/routes/pay/params', json=cp.Params().json()).status_code == 403
    assert pay.calls == []
    pay.role['value'] = 'admin'
    assert pay.get('/api/routes/pay').status_code == 200

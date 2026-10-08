# -*- coding: utf-8 -*-
"""«Աշխատավարձ» (docs/research/09-crew-pay.md, формула владельца 07.10): расчёт crew_pay, параметры в route_optimizer.db,
API страницы с подменённым загрузчиком ERP (живой ERP не нужен), CSV и доступ только администратору."""
import json
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import ErpError, check_sql, SQL_CREW_PAY  # noqa: E402
from route_optimizer.geo import haversine_km  # noqa: E402
from route_optimizer.snapshot import SnapshotCache  # noqa: E402
from test_route_optimizer import REF, _no_road_map, client, make_snapshot  # noqa: E402,F401

NOW = datetime(2026, 10, 7, 10, 0)
YEREVAN = timezone(timedelta(hours=4))
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
    assert cp.totals(()) == {'points': 0, 'tonnes': 0, 'km': 0, 'no_coords': 0, 'sales': 0, 'fix': 0, 'piece': 0, 'pay': 0,
                             'old': 0, 'diff': 0}


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
    assert (p.fix, p.rate_point, p.rate_tonne, p.rate_km, p.minimum, p.norm_per_day, p.old_fix, p.old_pct) == \
        (100_000, 210, 1_500, 15, 250_000, 12, 100_000, 2.0)


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


SIX = [1, 2, 3, 4, 5, 6]


def rest(today, workdays=SIX, holidays=()):
    """Рабочие дни календаря с today до конца месяца — тот же views._calendar_rest, что у страницы."""
    return views._calendar_rest(today, {'workdays': workdays, 'holidays': list(holidays)})


def test_calendar_rest():
    assert len(rest(date(2026, 10, 7))) == 22                                   # 07–31.10 без 11, 18, 25
    assert len(rest(date(2026, 10, 7), holidays=['2026-10-12', '2026-10-04'])) == 21
    assert len(rest(date(2026, 10, 7), workdays=[1, 2, 3, 4, 5])) == 18
    assert rest(date(2026, 10, 31)) == {date(2026, 10, 31)} and rest(date(2026, 10, 25)) == set(rest(date(2026, 10, 26)))
    assert len(rest(date(2026, 12, 1), holidays=['2026-12-31'])) == 26          # граница года


def test_current_month_prorated_by_whole_month_not_elapsed_days():
    """07.10: доставки 1–3, 5–7 (6 дней) + календарь 07–31.10 (22 дня, 07.10 в обоих) — D_month 27, а не 6."""
    p = cp.Params(rate_point=250, rate_tonne=1750)
    week = data(*avakimyan_week())
    before = row(cp.compute(week, p), KORYUN)                         # без D_month — как было: полный месяц за неделю
    assert (before.days, before.points, round(before.tonnes, 3)) == (6, 75, 9.7)
    assert before.fix == 100_000 and before.minimum == 250_000 and before.pay == 250_000
    res = cp.compute(week, p, rest(date(2026, 10, 7)))
    r = row(res, KORYUN)
    assert (res.workdays, res.workdays_month) == (6, 27)
    assert r.fix == cp.money(100_000 * 6 / 27) == 22_222
    assert r.piece == cp.money(250 * 75 + 1750 * 9.7) == 35_725
    assert r.minimum == cp.money(250_000 * 75 / (12 * 27)) == 57_870
    assert r.pay == 57_947 and not r.min_applied
    assert r.old == 22_222 + cp.money(0.02 * 75)


def test_mid_month_past_days_by_fact_future_by_calendar():
    """09.10, доставок сегодня ещё нет: 08.10 (рабочий по календарю) без доставок — не в D_month; 09.10 — как будущий день
    календаря: 6 + 20 (09–31.10 без воскресений) = 26."""
    res = cp.compute(data(*avakimyan_week()), cp.Params(), rest(date(2026, 10, 9)))
    assert (res.workdays, res.workdays_month) == (6, 26)
    assert row(res, KORYUN).fix == cp.money(100_000 * 6 / 26)


def test_sunday_delivery_counts_as_workday():
    """Доставка в воскресенье 04.10 — рабочий день по факту: 7 + 21 будущих (08–31.10) = 28."""
    week = data(*avakimyan_week(), oct_inv(AGHVAN, 4, 900, kg=10))
    res = cp.compute(week, cp.Params(), rest(date(2026, 10, 7)))
    assert (res.workdays, res.workdays_month) == (7, 28)
    assert row(res, AGHVAN).fix == cp.money(100_000 / 28)


def test_month_workdays_never_below_elapsed_days():
    """D_month ⊇ дни с доставкой: пустой остаток календаря (праздники до конца месяца) или закрытый месяц — D."""
    week, p = data(*avakimyan_week()), cp.Params()
    for planned in (frozenset(), {date(2026, 10, 7)}, None):
        res = cp.compute(week, p, planned)
        assert res.workdays_month == 6 and res.rows == cp.compute(week, p).rows, planned
    assert cp.compute(data(), p, rest(date(2026, 10, 7))).workdays_month == 22
    assert cp.compute(data(), p).workdays_month == 0


def save_calendar(pay, **settings):
    store = pay.application.extensions['route_optimizer'].store
    changes, errors = st.validate_payload({'settings': settings}, store.load(), REF)
    assert not errors, errors
    store.save(changes, 'qa')


def set_today(monkeypatch, yerevan, server=None):
    monkeypatch.setattr(views, '_yerevan_now', lambda: yerevan)
    monkeypatch.setattr(views, '_clock', lambda: server or yerevan.replace(tzinfo=None))


def test_api_current_month_uses_calendar_of_routes_settings(pay):
    pay.source['invoices'] = avakimyan_week()
    body = pay.get('/api/routes/pay').get_json()
    assert body['current'] and (body['workdays'], body['workdays_month']) == (6, 27)      # 5 прошедших + 22 по календарю
    k = body['rows'][0]
    assert k['fix'] == cp.money(100_000 * 6 / 27) and k['minimum'] == cp.money(250_000 * 75 / (12 * 27))
    # праздник в будний день впереди — минус день; праздник в прошедшее воскресенье без доставок — ничего
    save_calendar(pay, holidays=['2026-10-12', '2026-10-04'])
    assert pay.get('/api/routes/pay').get_json()['workdays_month'] == 26
    # пятидневка: впереди 17 дней (без суббот и 12.10), прошедшая суббота 03.10 с доставкой — по факту: 5 + 17
    save_calendar(pay, workdays=[1, 2, 3, 4, 5])
    body = pay.get('/api/routes/pay').get_json()
    assert body['workdays_month'] == 22 and body['rows'][0]['fix'] == cp.money(100_000 * 6 / 22)
    lines = pay.get('/api/routes/pay.csv').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'Աշխատանքային օրեր ամսում;22' in lines and not any(x.startswith('Ստուգել;') for x in lines)
    assert any(x.startswith('B001/1;Իսկանդարյան Կորյուն;6;6;75;') for x in lines)      # в таблице — D (6)
    assert len(pay.calls) == 1                                                            # календарь — без нового чтения ERP


def test_last_day_of_current_month_equals_closed_month(pay, monkeypatch):
    """31.10 (сб) с доставками: текущий месяц = тот же месяц, открытый 01.11 как прошлый, до драма. 12.10 и прочие
    рабочие дни без доставок не в D_month — иначе 31.10 платили бы меньше, чем 01.11."""
    pay.source['invoices'] = avakimyan_week() + [oct_inv(KORYUN, 31, 500, kg=100), oct_inv(AGHVAN, 4, 501, kg=10)]
    set_today(monkeypatch, datetime(2026, 10, 31, 18, 0, tzinfo=YEREVAN))
    last = pay.get('/api/routes/pay').get_json()
    set_today(monkeypatch, datetime(2026, 11, 1, 9, 0, tzinfo=YEREVAN))
    closed = pay.get('/api/routes/pay?month=2026-10').get_json()
    assert last['current'] and not closed['current']
    assert (last['workdays'], last['workdays_month']) == (closed['workdays'], closed['workdays_month']) == (8, 8)
    assert last['rows'] == closed['rows'] and last['totals'] == closed['totals']


def test_pay_uses_yerevan_date_not_server_clock(pay, monkeypatch):
    """Сервер в UTC: 31.10 22:30, в Ереване уже 01.11 02:30 — текущий месяц ноябрь, читается по 01.11 включительно."""
    pay.source['invoices'] = []
    set_today(monkeypatch, datetime(2026, 11, 1, 2, 30, tzinfo=YEREVAN), server=datetime(2026, 10, 31, 22, 30))
    body = pay.get('/api/routes/pay').get_json()
    assert body['month'] == '2026-11' and body['current'] and body['months'][0] == '2026-11'
    assert pay.calls == [(date(2026, 11, 1), date(2026, 11, 2))]
    assert body['workdays_month'] == 25                                                    # ноябрь 2026: 30 − 5 воскресений
    assert pay.get('/api/routes/pay?month=2026-11').status_code == 200
    lines = pay.get('/api/routes/pay.csv').get_data().decode('utf-8')[1:].split('\r\n')
    assert lines[0] == 'Աշխատավարձ;2026-11' and lines[1] == 'Հաշվված է;2026-11-01 02:30'


def test_calendar_unreadable_falls_back_to_elapsed_days_with_warning(pay, monkeypatch):
    pay.source['invoices'] = avakimyan_week()
    store = pay.application.extensions['route_optimizer'].store

    def broken():
        raise st.StoreError('settings')
    monkeypatch.setattr(store, 'load', broken)
    r = pay.get('/api/routes/pay')
    body = r.get_json()
    assert r.status_code == 200 and (body['workdays'], body['workdays_month']) == (6, 6)
    assert body['calendar_warning'] == views.PAY_CALENDAR_WARNING and 'օրացույց' in body['calendar_warning']
    assert body['rows'][0]['fix'] == 100_000
    csv_lines = pay.get('/api/routes/pay.csv').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'Ստուգել;' + views.PAY_CALENDAR_WARNING in csv_lines
    past = pay.get('/api/routes/pay?month=2026-09').get_json()                     # прошлому месяцу календарь не нужен
    assert past['calendar_warning'] is None


def save_old_params_row(store, **values):
    """Строка crew_pay, как её сохраняла версия до км (без rate_km)."""
    import sqlite3
    old = {k: v for k, v in cp.Params().json().items() if k != 'rate_km'}
    store.save_crew_pay_params(cp.Params(), 'qa')
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = 'crew_pay'",
                     (json.dumps({**old, **values, 'updated_at': '2026-10-07T09:00:00', 'updated_by': 'boss'}),))


def test_past_month_unchanged_and_saved_params_win(pay):
    """Регрессия: сентябрь со ставками 175 / 1 200 из строки, сохранённой версией до км (без rate_km): строка читается
    с rate_km = 0 — формула владельца та же, что до км (он подбирал ставки без км), и сильнее новых значений по
    умолчанию; дороги не трогаются; D — как до D_month, даже если календарь настроек сентябрю не соответствует."""
    save_old_params_row(pay.state.store, rate_point=175, rate_tonne=1200)
    save_calendar(pay, workdays=[1, 2, 3, 4, 5], holidays=['2026-09-01', '2026-09-21'])
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['params']['rate_point'] == 175 and body['params']['rate_tonne'] == 1200 and body['params']['rate_km'] == 0
    assert body['params_updated_by'] == 'boss' and body['km_counted'] is False and body['km_warnings'] == []
    assert pay.state.roads.roads.ensured == []
    assert (body['workdays'], body['workdays_month']) == (2, 2)
    k, a = body['rows']
    assert (k['days'], k['points'], k['tonnes'], k['fix'], k['piece']) == (2, 2, 1.5, 100_000, 2_150)
    assert k['minimum'] == 20_833 and k['pay'] == 102_150 and k['old'] == 103_000 and k['diff'] == -850
    assert (a['days'], a['fix'], a['piece'], a['minimum'], a['pay'], a['old']) == (1, 50_000, 475, 10_417, 50_475, 50_400)
    lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'B001/1;Իսկանդարյան Կորյուն;2;2;2;1,500;0,0;100000;2150;20833;102150;;103000;-850' in lines
    assert 'Մեկ կմ-ի համար, ֏;0' in lines and 'Աշխատանքային օրեր ամսում;2' in lines


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
    assert st.CREW_PAY_KEY not in store.load().settings and st.SCHEMA_VERSION >= 24   # настройки маршрутов не задеты


def test_store_params_row_without_rate_km_loads(tmp_path):
    """Строки нет — значения по умолчанию (15 ֏/км). Строка версии до км (rate_km нет): rate_km = 0 — формула, которую
    владелец подобрал без км, не меняется; остальное — как сохранено; запись не «битая»."""
    store = st.Store(str(tmp_path / 'routes.db'))
    assert store.crew_pay_params()[0].rate_km == 15
    save_old_params_row(store, rate_point=250, rate_tonne=1750)
    params, at, by = store.crew_pay_params()
    assert (params.rate_point, params.rate_tonne, params.rate_km) == (250, 1750, 0) and by == 'boss'
    store.save_crew_pay_params(params, 'boss')                                     # пересохранена — rate_km уже в записи
    assert json.loads(store._read(lambda c: c.execute("SELECT value FROM settings WHERE key = 'crew_pay'").fetchone())[0]
                      )['rate_km'] == 0
    assert cp.check_params({**cp.Params().json(), 'rate_km': None})[1] == {'rate_km': 'Լրացրեք թիվը'}   # есть, но пусто
    assert set(cp.check_params({**cp.Params().json(), 'rate_km': 10_001})[1]) == {'rate_km'}
    assert cp.check_params({**cp.Params().json(), 'rate_km': 0})[0].rate_km == 0


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


DEPOT = (40.0, 44.5)
NO_COORD = 444                                    # клиент без координат


def shop(c):
    """Магазин c — в c км к северу от склада по одной «дороге» FakeRoads: тур дня = 2 × самый дальний магазин."""
    return (DEPOT[0] + c / 1000, DEPOT[1])


class FakeRoads:
    """RoadDistances в памяти (живые кэши data/roads/ не трогаются): км = |Δширота| × 1000; точки far — без км по
    дорогам (не привязаны). Считает вызовы ensure и km."""
    version, km_source = 'fake', 'osm'

    def __init__(self, far=(), failed=False):
        self.far, self.failed, self.ensured, self.km_calls = set(far), failed, [], 0

    def ensure(self, points):
        self.ensured.append(list(points))

    def km(self, a, b):
        self.km_calls += 1
        return None if self.failed or a in self.far or b in self.far else abs(a[0] - b[0]) * 1000


class FakeProvider:
    def __init__(self, roads):
        self.roads = roads

    def get(self):
        return self.roads


@pytest.fixture
def pay(client, monkeypatch):
    """Клиент «Маршрутов», «сегодня» — 07.10.2026; роль — из g.user_role (как ставит app_v2), по умолчанию admin.
    Склад DEPOT, магазины — shop(c) (ERP-адрес снимка; NO_COORD — без точки), дороги — FakeRoads."""
    monkeypatch.setattr(views, '_clock', lambda: NOW)
    monkeypatch.setattr(views, '_yerevan_now', lambda: NOW.replace(tzinfo=YEREVAN))
    app = client.application
    state = app.extensions['route_optimizer']
    snap = replace(make_snapshot(), erp_points={100_000 + c: (c, shop(c)) for c in range(1000) if c != NO_COORD},
                   default_address={c: 100_000 + c for c in range(1000)}, gps_points={})
    state.snapshots = SnapshotCache(lambda: snap)
    state.roads = FakeProvider(FakeRoads())
    changes, errors = st.validate_payload({'depot': {'lat': DEPOT[0], 'lon': DEPOT[1]}}, state.store.load(), REF)
    assert not errors, errors
    state.store.save(changes, 'qa')
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
    client.role, client.calls, client.source, client.state = role, calls, source, state
    return client


def test_api_month(pay):
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['success'] and body['month'] == '2026-09' and not body['current'] and body['workdays'] == 2
    assert body['workdays_month'] == 2                                              # закрытый месяц: D_month = D
    assert pay.calls == [(date(2026, 9, 1), date(2026, 10, 1))]
    assert body['months'][0] == '2026-10' and body['months'][-1] == '2025-11' and len(body['months']) == 12
    assert [r['code'] for r in body['rows']] == ['B001/1', 'B002/1']
    k = body['rows'][0]
    assert body['km_counted'] is True and body['km_warnings'] == []
    assert (k['days'], k['points'], k['tonnes'], k['km'], k['no_coords']) == (2, 2, 1.5, 6.0, 0)
    assert k['fix'] == 100_000 and k['piece'] == 210 * 2 + 1500 * 1.5 + 15 * 6 == 2_760
    assert k['minimum'] == 20_833 and k['pay'] == 102_760 and k['old'] == 103_000 and k['diff'] == -240
    assert [(d['date'], d['km'], d['piece']) for d in k['by_day']] == [('2026-09-01', 2.0, 210 + 1500 + 30),
                                                                       ('2026-09-02', 4.0, 210 + 750 + 60)]
    assert body['totals']['km'] == 12.0 and body['rows'][1]['km'] == 6.0
    assert body['totals']['pay'] == sum(r['pay'] for r in body['rows']) and body['params'] == cp.Params().json()
    pay.get('/api/routes/pay?month=2026-09')
    assert len(pay.calls) == 1                                                      # повтор — из кэша


def test_api_current_month_until_today_and_bad_months(pay):
    pay.source['invoices'] = []
    body = pay.get('/api/routes/pay').get_json()
    assert body['month'] == '2026-10' and body['current'] and body['workdays'] == 0 and body['rows'] == []
    assert body['workdays_month'] == 22 and body['calendar_warning'] is None   # пн–сб с 07.10 по 31.10
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
    assert 'B001/1;Իսկանդարյան Կորյուն;2;2;2;1,500;6,0;100000;2760;20833;102760;;103000;-240' in table
    assert lines[head].startswith('Կոդ;Առաքիչ;Օրեր;Աշխատանքային օրեր;Կետեր;Տոննա;Կմ;Ֆիքս;')
    assert 'Մեկ կմ-ի համար, ֏;15' in lines and 'Մեկ կետի համար, ֏;210' in lines
    assert 'Աշխատանքային օրեր ամսում;2' in lines[:head]
    assert table[-1].startswith(';Ընդամենը;;2;5;1,770;42,0;')            # 6 + 6 + 14 + 16 км


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


# ============================== плановые км ==============================

def test_tour_km_on_tiny_fake_distance():
    """Тур склад → магазины → склад по NN + 2-opt: «манхэттен» на квадрате — обход по периметру (4), а не крест-накрест."""
    manhattan = lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1])   # noqa: E731
    depot = (0.0, 0.0)
    assert cp.tour_km(depot, [(1.0, 1.0), (0.0, 1.0), (1.0, 0.0)], manhattan) == (4.0, 0)
    assert cp.tour_km(depot, [], manhattan) == (0.0, 0)
    assert cp.tour_km(depot, [(0.0, 3.0)], manhattan) == (6.0, 0)
    # пары без км по дорогам — по прямой × 1,3, и их число
    a, b = (40.0, 44.5), (40.01, 44.5)
    km, straight = cp.tour_km(a, [b], lambda x, y: None)
    assert straight == 2 and km == pytest.approx(2 * haversine_km(a, b) * cp.KM_DETOUR)


def test_plan_tours_missing_coords_and_memo():
    calls = []

    def road(a, b):
        calls.append((a, b))
        return abs(a[0] - b[0]) * 1000
    coords = {c: shop(c) for c in (1, 2, 5)}
    days = [frozenset({1, 2, NO_COORD}), frozenset({NO_COORD}), frozenset({5})]
    memo = {}
    tours, straight = cp.plan_tours(DEPOT, coords, days, road, memo)
    assert straight == 0 and tours.no_coords == {NO_COORD}
    assert {k: round(v, 6) for k, v in tours.km.items()} == {days[0]: 4.0, days[1]: 0.0, days[2]: 10.0}
    n = len(calls)
    again, _ = cp.plan_tours(DEPOT, coords, [frozenset({1, 2}), frozenset({5})], road, memo)   # те же точки — из memo
    assert len(calls) == n and round(again.km[frozenset({1, 2})], 6) == 4.0


def sept_tours(data_, p=cp.Params(), coords=None):
    coords = coords if coords is not None else {c: shop(c) for c in range(1000) if c != NO_COORD}
    return cp.plan_tours(DEPOT, coords, cp.day_stores(data_, p), lambda a, b: abs(a[0] - b[0]) * 1000)[0]


def test_missing_coords_zero_km_counted_per_row_and_day():
    d = data(inv(KORYUN, 1, 3, kg=100), inv(KORYUN, 1, NO_COORD, kg=100), inv(KORYUN, 2, NO_COORD, kg=100),
             inv(AGHVAN, 2, 7, kg=100))
    res = cp.compute(d, cp.Params(), tours=sept_tours(d))
    k = row(res, KORYUN)
    assert res.km_counted and k.no_coords == 2 and k.km == pytest.approx(6.0)
    assert [(x.day.day, round(x.km, 6), x.no_coords) for x in k.by_day] == [(1, 6.0, 1), (2, 0.0, 1)]
    assert k.piece == cp.money(210 * 3 + 1500 * 0.3 + 15 * 6)
    assert row(res, AGHVAN).no_coords == 0 and cp.totals(res.rows)['no_coords'] == 2


def test_rate_km_zero_no_km_and_no_tours_needed():
    d = data(inv(KORYUN, 1, 3, kg=100))
    p = cp.Params(rate_km=0)
    res = cp.compute(d, p, tours=sept_tours(d))
    assert not res.km_counted and row(res, KORYUN).km == 0 and row(res, KORYUN).piece == 210 + 150
    assert not cp.compute(d, cp.Params()).km_counted                          # туров нет — км не в сдельной части


def test_merged_person_sums_km_of_both_codes():
    """Կարապետ сменил код: одна строка, км — сумма дней обоих кодов."""
    agents = {**AGENTS, 21: ('B004/19', 'Հակոբյան Կարապետ'), 22: ('B003/24', 'Հակոբյան Կարապետ')}
    d = cp.CrewData((inv(21, 1, 5, kg=100), inv(21, 1, 6, kg=60), inv(22, 2, 5, kg=50), inv(22, 3, 9, kg=10)), agents)
    res = cp.compute(d, cp.Params(), tours=sept_tours(d))
    assert len(res.rows) == 1
    r = res.rows[0]
    assert r.agent_ids == (22, 21) and r.km == pytest.approx(12 + 10 + 18)
    assert [round(x.km, 6) for x in r.by_day] == [12.0, 10.0, 18.0]


def test_current_month_proration_unaffected_by_km():
    """Км — только в сдельной части: фикс, минимум и D_month текущего месяца те же, что без км."""
    week = data(*avakimyan_week())
    plain = row(cp.compute(week, cp.Params(), rest(date(2026, 10, 7))), KORYUN)
    res = cp.compute(week, cp.Params(), rest(date(2026, 10, 7)), sept_tours(week))
    r = row(res, KORYUN)
    assert (res.workdays, res.workdays_month) == (6, 27)
    assert (r.fix, r.minimum, r.old) == (plain.fix, plain.minimum, plain.old) == (22_222, 57_870, plain.old)
    assert r.km > 0 and r.piece == cp.money(210 * 75 + 1500 * 9.7 + 15 * r.km)


def test_api_km_warnings_for_missing_coords_and_csv(pay):
    pay.source['invoices'] = SEPT + [inv(KORYUN, 2, NO_COORD, kg=10)]
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    k = body['rows'][0]
    assert (k['points'], k['km'], k['no_coords']) == (3, 6.0, 1)
    assert [(d['km'], d['no_coords']) for d in k['by_day']] == [(2.0, 0), (4.0, 1)]
    assert body['totals']['no_coords'] == 1
    assert body['km_warnings'] == ['1 կետ առանց կոորդինատների — կմ-ն պակաս է հաշվված (Իսկանդարյան Կորյուն՝ 1)։']
    lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'Ստուգել;' + body['km_warnings'][0] in lines
    assert any(x.startswith('B001/1;Իսկանդարյան Կորյուն;2;2;3;1,510;6,0;') for x in lines)


def test_api_km_cached_with_erp_data(pay):
    """Туры месяца живут вместе с данными ERP: повтор, CSV и правка ставок дороги не пересчитывают."""
    roads = pay.state.roads.roads
    first = pay.get('/api/routes/pay?month=2026-09').get_json()
    calls = roads.km_calls
    assert calls > 0 and roads.ensured and set(map(tuple, roads.ensured[0])) == {DEPOT, shop(1), shop(2), shop(3)}
    pay.get('/api/routes/pay.csv?month=2026-09')
    assert pay.post('/api/routes/pay/params', json={**cp.Params().json(), 'rate_km': 20}).status_code == 200
    again = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert roads.km_calls == calls and len(pay.calls) == 1
    assert again['rows'][0]['km'] == first['rows'][0]['km'] and again['rows'][0]['piece'] == 2_760 + 5 * 6


def test_api_rate_km_zero_skips_roads(pay):
    assert pay.post('/api/routes/pay/params', json={**cp.Params().json(), 'rate_km': 0}).status_code == 200
    pay.state.snapshots = SnapshotCache(lambda: (_ for _ in ()).throw(ErpError('снимок не нужен')))
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['km_counted'] is False and body['km_warnings'] == [] and body['rows'][0]['km'] == 0
    assert body['rows'][0]['piece'] == 210 * 2 + 1500 * 1.5
    assert pay.state.roads.roads.ensured == [] and pay.state.roads.roads.km_calls == 0


@pytest.mark.parametrize('broken', ['no_map', 'failed'])
def test_api_roads_unavailable_haversine_fallback(pay, broken):
    pay.state.roads = None if broken == 'no_map' else FakeProvider(FakeRoads(failed=True))
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['km_warnings'] == [views.PAY_KM_NO_ROADS] and 'ճանապարհների քարտեզ չկա' in views.PAY_KM_NO_ROADS
    k = body['rows'][0]
    expect = 2 * cp.KM_DETOUR * (haversine_km(DEPOT, shop(1)) + haversine_km(DEPOT, shop(2)))
    assert k['km'] == round(expect, 1) and k['piece'] == cp.money(210 * 2 + 1500 * 1.5 + 15 * expect)
    lines = pay.get('/api/routes/pay.csv?month=2026-09').get_data().decode('utf-8')[1:].split('\r\n')
    assert 'Ստուգել;' + views.PAY_KM_NO_ROADS in lines


def test_api_unsnapped_store_partial_warning(pay):
    pay.state.roads = FakeProvider(FakeRoads(far={shop(2)}))
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert len(body['km_warnings']) == 1 and body['km_warnings'][0].startswith('Կմ-ն մասամբ մոտավոր է․ 2 հատված')
    assert body['rows'][0]['km'] == round(2 + 2 * cp.KM_DETOUR * haversine_km(DEPOT, shop(2)), 1)


def test_api_no_depot_no_km_with_warning(pay):
    store = pay.state.store
    changes, errors = st.validate_payload({'depot': None}, store.load(), REF)
    assert not errors
    store.save(changes, 'qa')
    body = pay.get('/api/routes/pay?month=2026-09').get_json()
    assert body['km_counted'] is False and body['km_warnings'] == [views.PAY_KM_NO_DEPOT]
    assert body['rows'][0]['piece'] == 210 * 2 + 1500 * 1.5 and pay.state.roads.roads.ensured == []


@pytest.mark.parametrize('failure', ['snapshot', 'roads'])
def test_api_km_failure_keeps_pay_without_km(pay, failure):
    """Сбой снимка ERP (координаты) или дорог — зарплата без км с предупреждением «Կմ-ն չհաշվվեց», страница и CSV работают."""
    if failure == 'snapshot':
        pay.state.snapshots = SnapshotCache(lambda: (_ for _ in ()).throw(ErpError('нет связи')))
    else:
        def broken(points):
            raise RuntimeError('граф')
        pay.state.roads.roads.ensure = broken
    r = pay.get('/api/routes/pay?month=2026-09')
    body = r.get_json()
    assert r.status_code == 200 and body['km_counted'] is False and body['rows'][0]['km'] == 0
    assert body['rows'][0]['piece'] == 210 * 2 + 1500 * 1.5
    assert len(body['km_warnings']) == 1 and body['km_warnings'][0].startswith(views.PAY_KM_FAILED)
    assert ('ERP' in body['km_warnings'][0]) == (failure == 'snapshot')
    csv = pay.get('/api/routes/pay.csv?month=2026-09')
    assert csv.status_code == 200
    assert 'Ստուգել;' + body['km_warnings'][0] in csv.get_data().decode('utf-8')[1:].split('\r\n')

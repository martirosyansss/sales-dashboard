# -*- coding: utf-8 -*-
"""«Առաքիչների KPI» (route_optimizer/crew_kpi.py): показатели առաքիչ из расчёта «Աշխատավարձ», медиана и оценка,
сравнение с прошлым месяцем и тренд; API страницы с подменённым загрузчиком ERP и доступ только администратору."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import crew_kpi as ck  # noqa: E402
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer.erp import ErpError  # noqa: E402
from test_route_crew_pay import AGENTS as PAY_AGENTS, AGHVAN, KORYUN, LINE, pay  # noqa: E402,F401
from test_route_optimizer import _no_road_map, client  # noqa: E402,F401

P = cp.Params()
ARTUR, NEW = 31, 32          # B008/10 из AGENTS «Աշխատավարձ» исключён по умолчанию — берём других
AGENTS = {**PAY_AGENTS, ARTUR: ('B007/18', 'Ավակիմյան Արթուր'), NEW: ('B009/1', 'Նորեկ')}


def inv(van, month, day, customer, kg=0.0, total=1.0):
    return cp.Invoice(van, date(2026, month, day), customer, LINE, total, kg)


def month(first, *invoices, agents=AGENTS):
    return ck.Month(first, P.norm_per_day, cp.compute(cp.CrewData(tuple(invoices), agents), P))


def person(rep, name):
    return next(p for p in rep.people if p.name == name)


def full_days(van, mon, days, stores_per_day, kg=100.0, total=1000.0):
    """stores_per_day магазинов в каждый из days дней месяца mon."""
    return [inv(van, mon, d, 100 * d + s, kg=kg, total=total) for d in range(1, days + 1) for s in range(stores_per_day)]


# ============================== расчёт ==============================

def test_person_kpis_add_up_from_pay_rows():
    sept = month(date(2026, 9, 1), *full_days(KORYUN, 9, 4, 10, kg=150), *full_days(AGHVAN, 9, 5, 8))
    rep = ck.report([sept])
    k = person(rep, 'Իսկանդարյան Կորյուն')
    row = next(r for r in sept.result.rows if KORYUN in r.agent_ids)
    assert (k.now.days, k.now.points, k.now.points_day) == (4, 40, 10)
    assert k.now.attendance == 4 / 5                                   # D = 5: Աղվան возил 5 дней
    assert k.now.tonnes_day == pytest.approx(1.5) and k.now.kg_point == pytest.approx(150)
    assert k.now.sales_day == 10_000 and k.now.norm == pytest.approx(40 / (P.norm_per_day * 4))
    assert k.now.pay == row.pay and k.now.cost_tonne == pytest.approx(row.pay / 6)
    assert k.prev is None and k.trend == (10,) and len(k.by_day) == 4
    assert rep.prev_team is None and rep.team.people == 2 and rep.team.workdays == 5


def test_median_and_grades_skip_people_with_few_days():
    sept = month(date(2026, 9, 1), *full_days(KORYUN, 9, 5, 14), *full_days(AGHVAN, 9, 5, 10),
                 *full_days(ARTUR, 9, 5, 7), inv(NEW, 9, 1, 9001), inv(NEW, 9, 2, 9002))
    rep = ck.report([sept])
    assert rep.team.median_points_day == 10                           # 14, 10, 7 — новичок с 2 днями не в медиане
    grades = {p.name: p.grade for p in rep.people}
    assert grades == {'Իսկանդարյան Կորյուն': 'good', 'Համբարձումյան Աղվան': 'mid', 'Ավակիմյան Արթուր': 'bad',
                      'Նորեկ': 'fewdays'}
    assert person(rep, 'Ավակիմյան Արթուր').vs_median == pytest.approx(0.7)
    assert [p.name for p in rep.people][-1] == 'Նորեկ'               # «քիչ օր» — в конце


def test_previous_month_and_trend_follow_the_person_across_code_change():
    agents = {**AGENTS, 21: ('B004/19', 'Հակոբյան Կարապետ'), 22: ('B003/24', 'Հակոբյան  Կարապետ')}
    months = [month(date(2026, 7, 1), *full_days(21, 7, 4, 6), agents=agents),
              month(date(2026, 8, 1), *full_days(KORYUN, 8, 3, 9), agents=agents),   # Կարապետ в августе не возил
              month(date(2026, 9, 1), *full_days(22, 9, 4, 12), agents=agents)]       # новый код, то же имя
    rep = ck.report(months)
    k = person(rep, 'Հակոբյան Կարապետ')                              # имя ERP без лишних пробелов
    assert k.trend == (6, None, 12) and k.prev is None
    assert rep.prev_team is not None and rep.prev_team.median_points_day == 9


def test_previous_month_stats():
    months = [month(date(2026, 8, 1), *full_days(KORYUN, 8, 4, 8, kg=100)),
              month(date(2026, 9, 1), *full_days(KORYUN, 9, 4, 12, kg=100))]
    k = person(ck.report(months), 'Իսկանդարյան Կորյուն')
    assert k.prev.points_day == 8 and k.now.points_day == 12 and k.trend == (8, 12)


def test_namesakes_counted_apart_stay_apart():
    agents = {**AGENTS, 21: ('B010/5', 'Հարությունյան Արմեն'), 22: ('B003/27', 'Հարությունյան Արմեն')}
    sept = month(date(2026, 9, 1), *full_days(21, 9, 4, 10), *full_days(22, 9, 4, 6), agents=agents)
    rep = ck.report([sept])
    assert sorted((p.code, p.now.points_day) for p in rep.people) == [('B003/27', 6), ('B010/5', 10)]
    assert len({p.key for p in rep.people}) == 2


def test_namesakes_split_in_one_month_still_find_last_month_by_code():
    """Август: возил только B010/5 (ключ по имени). Сентябрь: тёзки с общими днями, ключи по имени и коду — B010/5 всё равно
    находит свой август, у B003/27 прошлого месяца нет."""
    agents = {**AGENTS, 21: ('B010/5', 'Հարությունյան Արմեն'), 22: ('B003/27', 'Հարությունյան Արմեն')}
    rep = ck.report([month(date(2026, 8, 1), *full_days(21, 8, 4, 8), agents=agents),
                     month(date(2026, 9, 1), *full_days(21, 9, 4, 10), *full_days(22, 9, 4, 6), agents=agents)])
    by_code = {p.code: p for p in rep.people}
    assert by_code['B010/5'].prev.points_day == 8 and by_code['B010/5'].trend == (8, 10)
    assert by_code['B003/27'].prev is None and by_code['B003/27'].trend == (None, 6)


def test_namesakes_split_last_month_found_by_code_this_month():
    agents = {**AGENTS, 21: ('B010/5', 'Հարությունյան Արմեն'), 22: ('B003/27', 'Հարությունյան Արմեն')}
    rep = ck.report([month(date(2026, 8, 1), *full_days(21, 8, 4, 8), *full_days(22, 8, 4, 6), agents=agents),
                     month(date(2026, 9, 1), *full_days(22, 9, 4, 7), agents=agents)])
    (only,) = rep.people
    assert only.code == 'B003/27' and only.prev.points_day == 6 and only.trend == (6, 7)


def test_empty_month_is_empty_not_division_by_zero():
    rep = ck.report([month(date(2026, 8, 1)), month(date(2026, 9, 1))])
    assert rep.people == () and rep.prev_team is None
    assert (rep.team.median_points_day, rep.team.tonnes_day, rep.team.norm, rep.team.cost_tonne) == (None,) * 4


def test_zero_tonnes_has_no_cost_per_tonne():
    rep = ck.report([month(date(2026, 9, 1), *full_days(KORYUN, 9, 3, 5, kg=0, total=1000))])
    k = person(rep, 'Իսկանդարյան Կորյուն')
    assert k.now.cost_tonne is None and k.now.kg_point == 0 and rep.team.cost_tonne is None


# ============================== API ==============================

INVOICES = [*full_days(KORYUN, 8, 3, 8, kg=100), *full_days(KORYUN, 9, 4, 12, kg=100), *full_days(AGHVAN, 9, 4, 9),
            inv(NEW, 9, 1, 7001)]


@pytest.fixture
def kpi(pay):
    """Загрузчик отдаёт только накладные запрошенного периода (как ERP)."""
    state = pay.application.extensions['route_optimizer']

    def loader(since, until):
        pay.calls.append((since, until))
        if pay.source['error']:
            raise pay.source['error']
        return cp.CrewData(tuple(i for i in INVOICES if since <= i.day < until), AGENTS)
    state.crew_pay_loader = loader
    state.crew_pay_cache.clear()
    return pay


def test_api_month_with_trend(kpi):
    body = kpi.get('/api/routes/araqich?month=2026-09').get_json()
    assert body['success'] and body['month'] == '2026-09' and not body['current']
    assert body['trend_months'] == ['2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']
    assert kpi.calls == [(date(2026, m, 1), date(2026, m + 1, 1)) for m in range(4, 10)]
    assert body['months'][0] == '2026-10' and len(body['months']) == 12
    assert body['min_days'] == ck.MIN_DAYS and body['norm_per_day'] == P.norm_per_day
    people = {p['name']: p for p in body['people']}
    k = people['Իսկանդարյան Կորյուն']
    assert k['now']['points_day'] == 12 and k['prev']['points_day'] == 8
    assert k['trend'] == [None, None, None, None, 8, 12] and k['grade'] == 'good'     # 12 ≥ 110% медианы 10,5
    assert people['Համբարձումյան Աղվան']['grade'] == 'mid'
    assert people['Նորեկ']['grade'] == 'fewdays' and people['Նորեկ']['prev'] is None
    assert body['team']['median_points_day'] == 10.5 and body['prev_team']['median_points_day'] == 8
    assert [d['points'] for d in k['by_day']] == [12, 12, 12, 12]
    kpi.get('/api/routes/araqich?month=2026-09')
    assert len(kpi.calls) == 6                                                     # повтор — из кэша «Աշխատավարձ»


def test_api_shares_cache_with_pay_page(kpi):
    kpi.get('/api/routes/pay?month=2026-09')
    kpi.get('/api/routes/araqich?month=2026-09')
    assert kpi.calls.count((date(2026, 9, 1), date(2026, 10, 1))) == 1


def test_trend_months_do_not_evict_each_other_with_pay_page(kpi):
    """Самый ранний выбор (11 месяцев назад) + 5 месяцев тренда до него и текущий месяц: 17 месяцев помещаются в кэш,
    повторное открытие текущего месяца ERP не читает."""
    kpi.get('/api/routes/araqich')                            # май–октябрь
    kpi.get('/api/routes/araqich?month=2025-11')              # июнь–ноябрь 2025
    kpi.get('/api/routes/pay?month=2026-05')                  # уже в кэше
    n = len(kpi.calls)
    kpi.get('/api/routes/araqich')
    assert len(kpi.calls) == n == 12
    assert len(kpi.application.extensions['route_optimizer'].crew_pay_cache) == 12


def test_cache_is_lru(kpi, monkeypatch):
    from route_optimizer import views
    monkeypatch.setattr(views, 'PAY_CACHE_MONTHS', 2)
    kpi.get('/api/routes/pay?month=2026-08')
    kpi.get('/api/routes/pay?month=2026-09')
    kpi.get('/api/routes/pay?month=2026-08')                  # август снова свежий — вытесняется сентябрь
    kpi.get('/api/routes/pay?month=2026-07')
    kpi.get('/api/routes/pay?month=2026-08')
    assert kpi.calls.count((date(2026, 8, 1), date(2026, 9, 1))) == 1


def test_api_current_month_and_calendar_warning(kpi, monkeypatch):
    from route_optimizer import store as st
    from route_optimizer import views
    body = kpi.get('/api/routes/araqich').get_json()
    assert body['month'] == '2026-10' and body['current'] and body['calendar_warning'] is None
    assert body['trend_months'][-1] == '2026-10' and kpi.calls[-1] == (date(2026, 10, 1), date(2026, 10, 8))
    store = kpi.application.extensions['route_optimizer'].store

    def broken():
        raise st.StoreError('settings')
    monkeypatch.setattr(store, 'load', broken)
    body = kpi.get('/api/routes/araqich').get_json()
    assert body['success'] and body['calendar_warning'] == views.PAY_CALENDAR_WARNING


def test_api_bad_month_and_erp_down(kpi):
    assert kpi.get('/api/routes/araqich?month=2024-01').status_code == 400
    assert kpi.get('/api/routes/araqich?month=xx').status_code == 400
    kpi.source['error'] = ErpError('down')
    r = kpi.get('/api/routes/araqich?month=2026-09')
    assert r.status_code == 503 and r.get_json()['success'] is False


@pytest.mark.parametrize('role', [None, 'garage', 'warehouse', 'user'])
def test_only_admin(kpi, role):
    kpi.role['value'] = role
    for path in ('/routes/araqich', '/api/routes/araqich', '/api/routes/araqich?month=2026-09'):
        assert kpi.get(path).status_code == 403, path
    assert kpi.calls == []
    kpi.role['value'] = 'admin'
    assert kpi.get('/api/routes/araqich').status_code == 200

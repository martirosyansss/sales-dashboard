# -*- coding: utf-8 -*-
"""«Աշխատավարձ» (docs/research/09-crew-pay.md, формула владельца 07.10): расчёт crew_pay, параметры в route_optimizer.db,
API страницы с подменённым загрузчиком ERP (живой ERP не нужен), CSV и доступ только администратору."""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from route_optimizer import crew_pay as cp  # noqa: E402
from route_optimizer import store as st  # noqa: E402
from route_optimizer import views  # noqa: E402
from route_optimizer.erp import ErpError, check_sql, SQL_CREW_PAY  # noqa: E402
from test_route_optimizer import _no_road_map, client  # noqa: E402,F401

NOW = datetime(2026, 10, 7, 10, 0)
LINE, LINE19, LINE19B = 1, 2, 3                   # менеджеры (линии)
KORYUN, AGHVAN, HELPER = 11, 12, 13               # экспедиторы
AGENTS = {LINE: ('A001/4', 'Մենեջեր'), LINE19: ('A008/3', '19 լ'), LINE19B: ('A008/6', '19 լ բ'),
          KORYUN: ('B001/1', 'Իսկանդարյան Կորյուն'), AGHVAN: ('B002/1', 'Համբարձումյան Աղվան'),
          HELPER: ('B008/10', 'Օգնական')}


def inv(van, day, customer, kg=0.0, total=0.0, line=LINE):
    return cp.Invoice(van, date(2026, 9, day), customer, line, total, kg)


def data(*invoices):
    return cp.CrewData(tuple(invoices), AGENTS)


def row(result, agent_id):
    return next(r for r in result.rows if r.agent_id == agent_id)


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
    assert res.workdays == 2 and [r.agent_id for r in res.rows] == [KORYUN, AGHVAN]   # по имени: Ի раньше Հ
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


# ============================== параметры в базе ==============================

def test_store_params_roundtrip_without_schema_change(tmp_path):
    store = st.Store(str(tmp_path / 'routes.db'))
    assert store.crew_pay_params() == cp.Params()
    p = cp.Params(fix=150_000, rate_point=220, rate_tonne=1500, excluded_people=())
    store.save_crew_pay_params(p)
    assert store.crew_pay_params() == p
    assert st.CREW_PAY_KEY not in store.load().settings and st.SCHEMA_VERSION == 24   # настройки маршрутов не задеты


def test_store_corrupt_params_is_an_error(tmp_path):
    import sqlite3
    store = st.Store(str(tmp_path / 'routes.db'))
    store.save_crew_pay_params(cp.Params())
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE settings SET value = '{\"fix\": -5}' WHERE key = 'crew_pay'")
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
    assert pay.calls == [(date(2026, 9, 1), date(2026, 10, 1))]
    assert body['months'][0] == '2026-10' and body['months'][-1] == '2025-11' and len(body['months']) == 12
    assert [r['code'] for r in body['rows']] == ['B001/1', 'B002/1']
    k = body['rows'][0]
    assert (k['days'], k['points'], k['tonnes'], k['fix'], k['piece']) == (2, 2, 1.5, 100_000, 2_150)
    assert k['minimum'] == 20_833 and k['pay'] == 102_150 and k['old'] == 103_000 and k['diff'] == -850
    assert [d['date'] for d in k['by_day']] == ['2026-09-01', '2026-09-02']
    assert body['totals']['pay'] == sum(r['pay'] for r in body['rows']) and body['params'] == cp.Params().json()
    pay.get('/api/routes/pay?month=2026-09')
    assert len(pay.calls) == 1                                                      # повтор — из кэша


def test_api_current_month_until_today_and_bad_months(pay):
    pay.source['invoices'] = []
    body = pay.get('/api/routes/pay').get_json()
    assert body['month'] == '2026-10' and body['current'] and body['workdays'] == 0 and body['rows'] == []
    assert pay.calls == [(date(2026, 10, 1), date(2026, 10, 8))]
    for bad in ('2025-10', '2026-11', '2026-13', 'x', '2026-9'):
        r = pay.get(f'/api/routes/pay?month={bad}')
        assert r.status_code == 400 and 'month' in r.get_json()['errors'], bad


def test_api_erp_down_is_an_error_not_zeros(pay):
    pay.source['error'] = ErpError('нет связи')
    r = pay.get('/api/routes/pay?month=2026-08')
    assert r.status_code == 503 and r.get_json()['success'] is False and 'rows' not in r.get_json()
    assert pay.get('/api/routes/pay.csv?month=2026-08').status_code == 503


def test_csv(pay):
    pay.source['invoices'] = SEPT + [cp.Invoice(99, date(2026, 9, 2), 7, LINE, 0, 0)]
    AGENTS[99] = ('B9', '=HYPERLINK("x")')
    try:
        r = pay.get('/api/routes/pay.csv?month=2026-09')
    finally:
        del AGENTS[99]
    assert r.status_code == 200 and r.mimetype == 'text/csv'
    assert 'crew-pay-2026-09.csv' in r.headers['Content-Disposition']
    text = r.get_data().decode('utf-8')
    assert text.startswith('﻿Կոդ;Առաքիչ;Օրեր;')
    lines = text[1:].strip().split('\r\n')
    assert len(lines) == 5 and 'Հին սխեմա (2%)' in lines[0]
    assert "'=HYPERLINK" in text                                                  # формула Excel не исполнится
    assert lines[2].startswith('B001/1;Իսկանդարյան Կորյուն;2;2;2;1,500;100000;2150;20833;102150;;103000;-850')
    assert lines[-1].startswith(';Ընդամենը;;2;')


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
    for path in ('/routes/pay', '/api/routes/pay', '/api/routes/pay.csv'):
        assert pay.get(path).status_code == 403, path
    assert pay.post('/api/routes/pay/params', json=cp.Params().json()).status_code == 403
    assert pay.calls == []
    pay.role['value'] = 'admin'
    assert pay.get('/api/routes/pay').status_code == 200

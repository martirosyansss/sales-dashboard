"""Регрессии проверки 02.10: смена, резервный план, предел загрузки и GPS вне шаблона."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import math

from route_optimizer import fleet as fl, search as sr, snapshot as ss, evaluate as ev
from test_route_optimizer import (_search_problem, _dp_ctx, _dp_stops, _info, HOWO,
                                  make_snapshot, _row, _bundle, client)


def test_shift_repair_preserves_visits_and_locked_days():
    current = [((1, 1), (2, 1)), ((1, 1), (2, 1))]
    alternative = ((1, 2), (2, 2))
    prob = _search_problem([None, None], current, [[current[0]], [current[1], alternative]],
                           locked=(0,), minutes=20.0)
    prob = replace(prob, hard_time=True, weights=replace(prob.weights, window_min=30.0))
    state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT]*2, change_penalty=0)
    assert sum(state.shift_excess().values()) == 20.0
    assert sr.repair_shift(state)
    assert state.pattern[0] == prob.lines[0].current
    assert all(len(p) == 2 for p in state.pattern)
    assert sum(state.shift_excess().values()) == 0.0
    # Экономически дешёвое возвращение в перегруженный день не становится допустимым.
    assert math.isinf(state.eval_relocate(1, prob.lines[1].current)[0])


def test_locked_impossible_shift_is_reported_without_dropping_visits():
    current = [((1, 1), (2, 1))]
    prob = _search_problem([None], current, [[current[0]]], locked=(0,), minutes=40.0)
    prob = replace(prob, hard_time=True, weights=replace(prob.weights, window_min=30.0))
    state = sr.State(prob, [line.current for line in prob.lines], [sr.NO_VISIT], change_penalty=0)
    assert not sr.repair_shift(state)
    assert state.pattern == [prob.lines[0].current]


def test_fallback_keeps_90_percent_and_solo_exception(monkeypatch):
    # Сбой/отсутствие решателя: предел обязан соблюдать и резервный расчёт.
    monkeypatch.setattr(fl.vrp, 'available', lambda: False)
    truck = replace(HOWO, capacity_kg=5000.0)
    ctx = _dp_ctx((truck,))
    stops, _ = _dp_stops([(1,(40.17,44.49),4967.3),(2,(40.1701,44.4901),26.316),
                          (3,(40.171,44.491),2400),(4,(40.172,44.492),2400)])
    from route_optimizer import dispatch as dp
    draft = dp.build(ctx, stops, None, [truck.car_code], 'now')
    view = dp.plan_view(ctx, stops, draft, _info)
    assert not view['unassigned'] and not view['overflow']['trips']
    trips = [t for row in view['trucks'] for t in row['trips']]
    for trip in trips:
        if trip['kg'] > .9*truck.capacity_kg+.5:
            assert len(trip['stops']) == 1 and trip['stops'][0]['customer_id'] == 1
    assert abs(view['summary']['kg'] - sum(s.kg for s in stops)) < 2


def test_rejected_solver_returns_fallback_and_logs(monkeypatch, caplog):
    # Ветка, которая раньше падала на неопределённом logger, проверяется явно.
    monkeypatch.setattr(fl.vrp, 'available', lambda: True)
    monkeypatch.setattr(fl.vrp, 'solve', lambda *a, **kw: [(0, [[0,0]])])
    ctx = _dp_ctx((HOWO,))
    trips = fl.route_day([(40.17,44.49)],[100.0],[10000.0],ctx.depot,[HOWO],ctx.norms,ctx.tn,
                         overflow=False,solver=True)
    assert len(trips) == 1 and trips[0].items == (0,)
    assert 'заказы не сходятся' in caplog.text


def test_snapshot_uses_good_gps_for_customer_outside_plan(monkeypatch):
    now = datetime(2026,10,2,12)
    visits = [ev.ActualVisit(999,7,(now-timedelta(days=i)).date(),now-timedelta(days=i),None,
                            40.17,44.49,20,'01') for i in range(3)]
    visits += [ev.ActualVisit(998,7,now.date(),now,None,40.17,44.49,200,'01')]*3
    answers={'agents':{},'route_templates':[],'customers':{},'customer_groups':{},'addresses':[],
             'sales_docs':[],'first_sale_dates':{},'monthly_revenue':{},'visits':visits,'tracks':{},
             'cars':{},'car_days':[],'car_last_used':{},'expeditors':{},'customer_debts':{}}
    for name,answer in answers.items():
        monkeypatch.setattr(ss.erp,name,lambda *a,answer=answer,**kw:answer)
    snap=ss.build_snapshot(object(),now.date(),'outside-plan',now)
    assert snap.plan.customer_ids == () or not snap.plan.customer_ids
    assert snap.gps_points[999] == (40.17,44.49)
    assert 998 not in snap.gps_points


def test_forecast_validation_does_not_train_on_test_sales():
    from route_optimizer import forecast, demand as dm, plan as pl
    start = date(2026,6,8)
    training = [dm.Order(101,start+timedelta(weeks=i),1,1000.0,10.0) for i in range(12)]
    holdout = [dm.Order(101,date(2026,8,31)+timedelta(weeks=i),1,2000.0,20.0) for i in range(4)]
    future = dm.Order(101,date(2026,9,30),1,1_000_000.0,100.0)
    snap = replace(make_snapshot(), today=date(2026,10,2),
                   plan=pl.build_plan([_row(1,11,1,1,1,101,1,1001)]),
                   validation_orders=tuple(training+holdout+[future]))
    result = forecast.revenue_validation(snap,[1])
    row = result['managers'][0]
    assert result['train_end'] == result['test_start'] == '2026-08-31'
    assert row['model_week'] == 1000 and row['fact_week'] == 2000
    assert row['error_pct'] == -50.0 and not row['ok']
    # Изменение факта и будущих продаж не меняет обученную модель.
    changed = replace(snap, validation_orders=tuple(training+[replace(o,revenue=o.revenue*10) for o in holdout]))
    assert forecast.revenue_validation(changed,[1])['managers'][0]['model_week'] == 1000


def test_forecast_missing_history_is_explicit():
    from route_optimizer.forecast import revenue_validation
    result = revenue_validation(make_snapshot(),[1])
    assert result['status'] == 'unavailable' and not result['ok']


def test_plan_export_checks_actual_accepted_plan_shift():
    from route_optimizer import optimize as opt
    bundle = _bundle()
    bundle = replace(bundle, settings=dict(bundle.settings,work_start='09:00',work_end='09:01'))
    result = opt.plan_export(make_snapshot(),bundle,[])
    assert not result['time_gate']['ok']
    assert result['time_gate']['days'] and result['rows']
    assert all(d['excess_minutes'] > 0 for d in result['time_gate']['days'])


def test_dispatch_coverage_reports_missing_coordinates():
    from route_optimizer import dispatch as dp
    stops, _ = _dp_stops([(1,(40.17,44.49),100),(2,None,250)])
    ctx = _dp_ctx((HOWO,))
    draft = dp.build(ctx,stops,None,[HOWO.car_code],'now')
    result = dp.plan_view(ctx,stops,draft,_info)
    coverage = result['coverage']
    assert not coverage['complete']
    assert (coverage['stops_total'],coverage['stops_assigned'],coverage['stops_no_coords']) == (2,1,1)
    assert coverage['kg_total'] == 350 and coverage['kg_no_coords'] == 250


def test_api_blocks_export_that_exceeds_shift(client, monkeypatch):
    from route_optimizer import views
    bundle = _bundle()
    bundle = replace(bundle, settings=dict(bundle.settings,work_start='09:00',work_end='09:01'))
    monkeypatch.setattr(views,'_bundle',lambda state:bundle)
    monkeypatch.setattr(views,'_roads',lambda *args:None)
    response = client.get('/api/routes/plan-export')
    assert response.status_code == 409
    body = response.get_json()
    assert body['code'] == 'shift_exceeded' and not body['success']
    assert not body['time_gate']['ok'] and body['time_gate']['days']

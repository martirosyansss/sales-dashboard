"""Local-only real dispatch API, synthetic orders and disposable SQLite."""
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

from flask import Flask, jsonify, render_template, request
from test_swap import DAY, SOURCE, dp, importlib, info, scenario


def create_app(directory):
    v = importlib.import_module('route_optimizer.views')
    Store = importlib.import_module('route_optimizer.store').Store
    ctx, stops, draft = scenario()
    draft.built_at = '2026-10-25T10:00:00'
    store = Store(str(Path(directory) / 'qa.sqlite'))
    store.save_dispatch(DAY.isoformat(), draft.to_json(), 'local-qa')
    state = NS(store=store)
    bundle = NS(settings={'workdays': [1,2,3,4,5,6]})

    def load(state_, bundle_, day, **kw):
        raw, rev = store.load_dispatch(day.isoformat())
        return NS(day=day, ctx=ctx, stops=stops, draft=kw.get('draft', dp.Draft.from_json(raw)),
                  rev=kw.get('rev', rev), deliver=[o for s in stops for o in s.orders],
                  backlog=[], carried=set(), auto=set(), ready=list(ctx.trucks),
                  data=NS(customers={s.customer_id:(str(s.customer_id),'QA '+str(s.customer_id)) for s in stops}), bundle=bundle)

    def body(dd):
        plan = dp.plan_view(ctx, stops, dd.draft, info)
        plan['baseline'] = None
        plan['built_at'] = dd.draft.built_at
        trucks = [dict(car_code=t.car_code, name='QA ' + t.car_code, ready=True, selected=True,
                       capacity_kg=t.capacity_kg, l100=t.l100, center_ok=True) for t in ctx.trucks.values()]
        return dict(day=DAY.isoformat(), today='2026-10-25', default_day=DAY.isoformat(), weekday=1,
            day_off=False, is_past=False, work_start='09:00', work_end='17:00', overtime_end='19:00',
            order_dates={'since':'2026-10-25','until':'2026-10-25'}, trucks=trucks, rev=dd.rev,
            depot={'lat':ctx.depot[0], 'lon':ctx.depot[1]}, problems=[], vehicle_options=trucks,
            orders=dict(count=4, kg=400, revenue=40000, customers=4, no_coords=0, no_coords_kg=0,
                agents_off=0, agents_off_kg=0, self_delivery=0, self_delivery_kg=0, excluded=0, shipped_before=0),
            stops_no_coords=[], excluded=[], agents=[], agents_off=[], agents_rule=True, backlog=[],
            backlog_days=5, orders_still_coming=False, ready_time='18:00', geo_suggestions=[],
            fresh=None, same_day=None, approved=dd.draft.approved, released=dd.draft.released,
            sent=dd.draft.sent, unsent=None, overtime=False, overtime_ok=False, plan=plan,
            store_unload={}, store_windows={}, vehicle_access={}, store_solo=[], store_center=[],
            crew={'drivers':{}, 'couriers':{}, 'trucks':{}, 'absent':[]}, drivers={}, couriers={},
            no_coords=[], settings_differ={}, fleet_rule={}, warehouse_available=False)

    v._state = lambda: state
    v._bundle = lambda _: bundle
    v._load_day = load
    v._stop_info = lambda dd: info
    v._today_min = lambda dd: None
    v._same_day_now = lambda: datetime(2026, 10, 25, 12)
    v._capture_prediction = lambda dd, draft: None
    v._dispatch_page_body = body

    app = Flask(__name__, template_folder=str(SOURCE / 'templates'), static_folder=str(SOURCE / 'static'))
    app.secret_key = 'local-synthetic-fixture-only'
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.context_processor(lambda: {'is_admin':True, 'current_user':None, 'current_username':'LOCAL QA'})
    app.add_url_rule('/logout', endpoint='logout', view_func=lambda: '')
    app.add_url_rule('/routes/dispatch', endpoint='qa_page', view_func=lambda: render_template('routes_dispatch.html'))
    app.add_url_rule('/api/routes/dispatch', endpoint='qa_get', view_func=lambda: jsonify(success=True, **body(load(state,bundle,DAY))))
    app.add_url_rule('/api/routes/dispatch/progress', endpoint='qa_progress', view_func=lambda: jsonify(success=True, trucks=[]))
    app.add_url_rule('/api/routes/dispatch/status', endpoint='qa_status', view_func=lambda: jsonify(success=True))
    app.add_url_rule('/api/routes/road-lines', endpoint='qa_roads', methods=['POST'],
                     view_func=lambda: jsonify(success=True, lines=[]))
    app.add_url_rule('/favicon.ico', endpoint='qa_icon', view_func=lambda: ('', 204))
    app.register_blueprint(v.bp)
    app.qa_store = store
    app.qa_ctx = ctx

    @app.post('/qa/scenario/<kind>')
    def qa_scenario(kind):
        original_ctx, original_stops, original_draft = scenario()
        ctx.trucks.clear()
        ctx.trucks.update(original_ctx.trucks)
        stops[:] = original_stops
        if kind == 'reserve':
            ctx.trucks['A'] = replace(ctx.trucks['A'], capacity_kg=2800)
            ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=2500)
            stops[:] = [replace(s, kg=1148.577, orders=tuple(replace(o, kg=1148.577) for o in s.orders))
                        if s.customer_id in (1, 2) else s for s in stops]
        elif kind == 'overload':
            ctx.trucks['B'] = replace(ctx.trucks['B'], capacity_kg=150)
        elif kind != 'normal':
            return ('', 400)
        store.save_dispatch(DAY.isoformat(), original_draft.to_json(), 'local-qa')
        return jsonify(success=True)
    return app


if __name__ == '__main__':
    app = create_app(tempfile.mkdtemp(prefix='dispatch-swap-qa-'))
    app.run(host='127.0.0.1', port=8811, use_reloader=False, debug=False)

"""Local QA only: deployed templates, actual refueling API, disposable SQLite."""
import ast
import base64
import importlib
import json
import sqlite3
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

from flask import Flask, g, jsonify, render_template, request

WORK = Path(__file__).resolve().parent
SOURCE = WORK.parents[2]
for name in ('route_optimizer', 'courier'):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(SOURCE / name)]
        sys.modules[name] = pkg
refuels = importlib.import_module('courier.refuels')
Store = importlib.import_module('courier.store').Store
clock = importlib.import_module('courier.clock')
clock.today = lambda: datetime(2026, 10, 27).date()


def gates():
    tree = ast.parse((SOURCE / 'app_v2.py').read_text(encoding='utf-8-sig'))
    names = {'_GARAGE_PAGE', '_REFUELS_PAGE', '_GARAGE_API', '_LIVE_PAGE', '_LIVE_API', '_DRIVERS_PAGE',
             '_DRIVERS_API', '_LIVE_ACK_API', '_WAREHOUSE_PAGE', '_WAREHOUSE_API', '_PUBLIC_STATIC',
             '_PUBLIC_GARAGE_API_RE', '_PUBLIC_WAREHOUSE_API_RE'}
    nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)
             or isinstance(n, ast.FunctionDef) and n.name in ('_garage_path_allowed', '_public_path_allowed', '_warehouse_path_allowed')]
    env = {'re': importlib.import_module('re')}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'actual-access-gates', 'exec'), env)
    return env


def event(conn, n, car, day, liters, odo, amount, supersedes=None, kind='refuel'):
    eid = f'10000000-0000-4000-8000-{n:012d}'
    at = day + 'T10:00:00+04:00'
    payload = dict(liters=liters, odometer_km=odo, amount_amd=amount, full_tank=True,
                   place='<img src=x onerror=alert(1)>')
    if supersedes:
        payload['supersedes'] = f'10000000-0000-4000-8000-{supersedes:012d}'
    conn.execute('INSERT INTO events(id,terminal_id,driver_id,car_code,date,type,at_device,at_utc,received_at,payload,flags) VALUES(?,1,1,?,?,?,?,?,?,?,?)',
                 (eid, car, day, kind, at, clock.utc_key(datetime.fromisoformat(at)), at, json.dumps(payload), '[]'))
    return eid


def create_app(directory=None):
    directory = Path(directory or tempfile.mkdtemp(prefix='refuels-local-qa-'))
    store = Store(str(directory / 'qa.db'))
    store.refuels()  # initialize only this disposable database
    with sqlite3.connect(store.path) as conn:
        if not conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]:
            conn.execute("INSERT INTO drivers(id,name,created_at,updated_at) VALUES(1,'Արամ <script>window.injected=1</script>','2026-01-01','2026-01-01')")
            event(conn, 1, '111AA11', '2026-09-30', 10, 1000, 5000)
            event(conn, 2, '111AA11', '2026-10-02', 12.5, 1000, 6250, 1)
            event(conn, 3, '111AA11', '2026-10-28', 13, 1000, 6500, 2)
            event(conn, 4, '111AA11', '2026-10-10', 25, 1400, None)
            event(conn, 5, '222BB22', '2026-10-20', 40, 2000, 24000)
            event(conn, 6, '111AA11', '2026-10-21', 0, 1500, 0, kind='delivery')
            for n in range(100, 155):
                event(conn, n, '333CC33', '2026-08-15', 1.1, 3000, 55.5)
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jI6kAAAAASUVORK5CYII=')
            Path(store.photos_dir).mkdir(exist_ok=True)
            (Path(store.photos_dir) / 'qa.png').write_bytes(png)
            for n, event_n, path in [(1, 5, 'qa.png'), (2, 6, 'qa.png'), (3, 5, '../qa.db')]:
                conn.execute('INSERT INTO photos VALUES(?,?,?,?,?,?,?,?)',
                             (f'20000000-0000-4000-8000-{n:012d}', f'10000000-0000-4000-8000-{event_n:012d}',
                              'photo', path, len(png), 'qa', 1, '2026-10-20'))
    app = Flask(__name__, template_folder=str(SOURCE / 'templates'), static_folder=str(SOURCE / 'static'))
    app.secret_key = 'local-synthetic-only'
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.extensions['courier'] = NS(store=store)
    env = gates()

    @app.before_request
    def role():
        g.user_role = request.headers.get('X-QA-Role', request.args.get('qa_role', 'admin'))
        if g.user_role == 'anonymous' and not request.path.startswith('/static/'):
            return jsonify(success=False, error='unauthorized'), 401
        if g.user_role == 'garage' and not request.path.startswith('/static/') and not env['_garage_path_allowed'](request.path, request.method):
            return jsonify(success=False), 403

    @app.context_processor
    def context():
        garage = g.user_role == 'garage'
        return dict(is_admin=not garage, is_garage=garage, is_public_role=garage, current_user=None, current_username='LOCAL QA')

    app.add_url_rule('/logout', endpoint='logout', view_func=lambda: '')
    app.add_url_rule('/favicon.ico', endpoint='qa_icon', view_func=lambda: ('', 204))
    app.add_url_rule('/routes/garage', endpoint='qa_garage', view_func=lambda: render_template('routes_garage.html'))
    app.register_blueprint(refuels.bp)
    return app


if __name__ == '__main__':
    app = create_app()
    # The unsafe-path attachment is exercised by unit tests, not the visual fixture.
    with sqlite3.connect(app.extensions['courier'].store.path) as conn:
        conn.execute("DELETE FROM photos WHERE id='20000000-0000-4000-8000-000000000003'")
    app.run(host='127.0.0.1', port=8817, use_reloader=False)

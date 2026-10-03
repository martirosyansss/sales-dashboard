"""Настоящий app_v2 под pytest не открывает данные сервера в корне репозитория (tests/conftest.py)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from conftest import ISOLATED, SERVER_FILES, SESSION_TMP, same_path  # noqa: E402


def test_app_v2_never_points_at_server_databases_or_map():
    import app_v2
    routes = app_v2.app.extensions['route_optimizer']
    paths = {'routes': routes.store.path, 'courier': app_v2.app.extensions['courier'].store.path,
             'map': routes.roads.path}
    for name, path in paths.items():
        assert not any(same_path(path, f) for f in SERVER_FILES.values()), (name, path)
        assert not same_path(Path(path).parent, ROOT), (name, path)
    assert not same_path(paths['routes'], ROOT / 'route_optimizer.db')
    assert not same_path(paths['courier'], ROOT / 'courier.db')
    server_valhalla = os.path.join(os.environ.get('PROGRAMDATA') or '~', 'route_optimizer', 'valhalla')
    assert not same_path(os.environ['ROUTES_VALHALLA_DIR'], server_valhalla)
    assert same_path(paths['routes'], os.environ['ROUTES_DB_PATH'])     # путь — из окружения, которое задал conftest
    assert same_path(paths['courier'], os.environ['COURIER_DB_PATH'])
    for name, path in (('ROUTES_DB_PATH', paths['routes']), ('COURIER_DB_PATH', paths['courier'])):
        assert name not in ISOLATED or same_path(Path(path).parent, SESSION_TMP), name

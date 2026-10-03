"""Изоляция тестов от данных сервера. app_v2 при импорте (route_optimizer.init_app, courier.init_app) берёт пути к базам
и карте из окружения, а без него — рядом с собой, в корне репозитория. В главном дереве там живые route_optimizer.db и
courier.db работающего сервера: тест, открывший их через настоящий app_v2 (store.load → _ensure_schema), создал бы или
МИГРИРОВАЛ базу под сервером прежней схемы. conftest загружается раньше тестовых модулей, поэтому до любого импорта
app_v2 переменные указывают во временную папку сессии:
- ROUTES_DB_PATH, COURIER_DB_PATH (у «Առաքիչ» рядом с базой — и папки фото и APK) — временные базы;
- ROUTES_OSM_PATH — несуществующая карта: тесты не зависят от карты на диске (кому нужна — задаёт свою), кэши
  расстояний рядом с картой сервера не пишутся, фоновая подготовка Valhalla при импорте app_v2 не запускается;
- ROUTES_VALHALLA_DIR — временная папка: по умолчанию это общая папка тайлов и матриц сервера (%PROGRAMDATA%).
Явно заданное значение сохраняется, кроме пути к данным сервера в корне репозитория (его база или карта)."""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_TMP = Path(tempfile.mkdtemp(prefix='sd-tests-'))
atexit.register(shutil.rmtree, SESSION_TMP, True)
# данные сервера в корне репозитория — тесты их не открывают
SERVER_FILES = {'ROUTES_DB_PATH': ROOT / 'route_optimizer.db', 'COURIER_DB_PATH': ROOT / 'courier.db',
                'ROUTES_OSM_PATH': ROOT / 'data' / 'roads' / 'armenia-latest.osm.pbf'}


def same_path(a: os.PathLike | str, b: os.PathLike | str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


ISOLATED: set[str] = set()   # переменные, которые задал этот conftest (остальные — вызывающий, и не на данные сервера)


def _isolate(name: str, temp: Path) -> None:
    value = os.environ.get(name)
    if not value or (name in SERVER_FILES and same_path(value, SERVER_FILES[name])):
        os.environ[name] = str(temp)
        ISOLATED.add(name)


_isolate('ROUTES_DB_PATH', SESSION_TMP / 'route_optimizer.db')
_isolate('COURIER_DB_PATH', SESSION_TMP / 'courier.db')
_isolate('ROUTES_OSM_PATH', SESSION_TMP / 'no-map.osm.pbf')
_isolate('ROUTES_VALHALLA_DIR', SESSION_TMP / 'valhalla')

# -*- coding: utf-8 -*-
"""Раздел «Маршруты» Sales Dashboard — оптимизатор маршрутов, этап 1 («как сейчас»).

Подключение в app_v2.py:
    import route_optimizer
    route_optimizer.init_app(app, db)

ERP читается только через route_optimizer.erp (read-only guard); свои настройки — SQLite.
Ни ERP, ни SQLite не трогаются при подключении: только при первом запросе к разделу.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask

from .roads import RoadProvider, osm_path
from .snapshot import ResultCache, SnapshotCache, load_snapshot
from .store import Store
from .views import EXTENSION_KEY, RoutesState, bp

logger = logging.getLogger(__name__)

DB_FILENAME = 'route_optimizer.db'


def init_app(app: Flask, db: Any, db_path: str | None = None) -> None:
    """Зарегистрировать Blueprint раздела.

    db — DatabaseConnection дашборда (берём только connection_string);
    db_path — файл SQLite; по умолчанию env ROUTES_DB_PATH или route_optimizer.db рядом с app_v2.py.
    Карта дорог — env ROUTES_OSM_PATH или data/roads/armenia-latest.osm.pbf (этап 5); файла нет —
    км по прямой × извилистость. Граф и матрица строятся при первом расчёте, а не здесь.
    """
    path = db_path or os.environ.get('ROUTES_DB_PATH') or os.path.join(app.root_path, DB_FILENAME)
    connection_string: str = db.connection_string
    roads = RoadProvider(osm_path())
    app.extensions[EXTENSION_KEY] = RoutesState(
        store=Store(path),
        snapshots=SnapshotCache(lambda: load_snapshot(connection_string)),
        results=ResultCache(),
        roads=roads,
    )
    app.register_blueprint(bp)
    logger.info('[Routes] Раздел «Маршруты» подключён; база настроек: %s; карта дорог: %s%s', path,
                roads.path, '' if os.path.exists(roads.path) else ' (нет — км по прямой)')

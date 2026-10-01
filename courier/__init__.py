# -*- coding: utf-8 -*-
"""Раздел «Առաքիչ» Sales Dashboard — сервер для Android-терминалов водителей.

Подключение в app_v2.py (рядом с route_optimizer):
    import courier
    courier.init_app(app, db)
и в глобальном before_request — courier.public_guard(request) и пропуск API_PREFIX мимо входа в дашборд.

ERP читается только через route_optimizer.erp (read-only guard); всё состояние терминалов — SQLite
courier.db (env COURIER_DB_PATH, по умолчанию рядом с app_v2.py), отдельно от route_optimizer.db.
Ни ERP, ни SQLite не трогаются при подключении: только при первом запросе.

Переменные окружения:
- COURIER_DB_PATH — файл базы;
- COURIER_PUBLIC_HOST — публичный хост туннеля (по умолчанию araqich.orix.am): с него открыт только API;
- COURIER_PUBLIC_URL — базовый URL API в QR (по умолчанию https://<COURIER_PUBLIC_HOST>/api/courier/v1);
- COURIER_DEMO=1 — тестовые данные /day для машины TEST на 2000-01-01 (контракт §4).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask, Request, Response

from . import api, erp_day, views
from .day import DayService
from .geo import DriverSource
from .routes_link import routes_view
from .state import API_PREFIX, EXTENSION_KEY, CourierState
from .store import Store

logger = logging.getLogger(__name__)

DB_FILENAME = 'courier.db'
DEFAULT_PUBLIC_HOST = 'araqich.orix.am'
ROUTES_EXTENSION = 'route_optimizer'

__all__ = ['API_PREFIX', 'driver_geo', 'init_app', 'public_guard']


def init_app(app: Flask, db: Any, db_path: str | None = None) -> None:
    """Зарегистрировать API терминалов и страницу офиса. db — DatabaseConnection дашборда (берём только
    connection_string)."""
    path = db_path or os.environ.get('COURIER_DB_PATH') or os.path.join(app.root_path, DB_FILENAME)
    cs: str = db.connection_string
    host = (os.environ.get('COURIER_PUBLIC_HOST') or DEFAULT_PUBLIC_HOST).strip().lower()
    url = os.environ.get('COURIER_PUBLIC_URL') or f'https://{host}/api/courier/v1'
    demo = os.environ.get('COURIER_DEMO') == '1'
    store = Store(path)
    days = DayService(store, lambda car, day, window, pick: erp_day.load_day(cs, car, day, window, pick),
                      lambda day: routes_view(app.extensions.get(ROUTES_EXTENSION), day), demo=demo)
    app.extensions[EXTENSION_KEY] = CourierState(
        store=store, days=days, public_host=host, public_url=url, demo=demo,
        catalog_loader=lambda today: erp_day.product_catalog(cs, today),
        cars_loader=lambda today: erp_day.cars_seen(cs, today),
        invoice_loader=lambda day: erp_day.invoice_cars(cs, day),
    )
    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)
    for code in (404, 405, 413):
        app.register_error_handler(code, api.json_http_error)
    logger.info('[Courier] Раздел «Առաքիչ» подключён; база: %s; публичный хост: %s%s', path, host,
                '; COURIER_DEMO=1' if demo else '')


def driver_geo(app: Flask) -> DriverSource:
    """Точки и предложения водителей для «Маршрутов» (driver-geo-plan.md §4) — после init_app:
    route_optimizer.attach_driver_geo(app, courier.driver_geo(app))."""
    return DriverSource(app.extensions[EXTENSION_KEY].store)


def is_public(request: Request, public_host: str) -> bool:
    """Запрос пришёл снаружи: Host — публичный хост туннеля или есть заголовок Cloudflare."""
    host = (request.host or '').split(':')[0].strip().lower()
    return host == public_host or 'Cf-Connecting-Ip' in request.headers


def public_guard(request: Request) -> Response | None:
    """Защита за туннелем: снаружи открыт только API терминалов; остальное — 404 (дашборд не виден)."""
    if request.path.startswith(API_PREFIX):
        return None
    from flask import current_app
    st = current_app.extensions.get(EXTENSION_KEY)
    host = st.public_host if st is not None else (os.environ.get('COURIER_PUBLIC_HOST') or DEFAULT_PUBLIC_HOST).lower()
    if is_public(request, host):
        return Response('Not Found', status=404, mimetype='text/plain')
    return None

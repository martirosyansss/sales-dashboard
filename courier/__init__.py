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
- COURIER_PUBLIC_HOST — публичный хост туннеля (по умолчанию araqich.orix.am): с него открыт только API (и то, что
  app_v2 разрешает в public_guard: журнал гаража из интернета, №53);
- COURIER_PUBLIC_URL — базовый URL API в QR (по умолчанию https://<COURIER_PUBLIC_HOST>/api/courier/v1);
- COURIER_DEMO=1 — тестовые данные /day для машины TEST на 2000-01-01 (контракт §4).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from flask import Flask, Request, Response

from . import api, erp_day, views
from .day import DayService
from .facts import FactsSource
from .geo import DriverSource
from .live import LiveSource
from .routes_link import routes_bundle, routes_view
from .state import API_PREFIX, EXTENSION_KEY, CourierState
from .store import Store

logger = logging.getLogger(__name__)

DB_FILENAME = 'courier.db'
DEFAULT_PUBLIC_HOST = 'araqich.orix.am'
ROUTES_EXTENSION = 'route_optimizer'

__all__ = ['API_PREFIX', 'driver_geo', 'fleet_facts', 'init_app', 'is_public_request', 'live_facts', 'not_found',
           'public_guard']


def init_app(app: Flask, db: Any, db_path: str | None = None) -> None:
    """Зарегистрировать API терминалов и страницу офиса. db — DatabaseConnection дашборда (берём только
    connection_string)."""
    path = db_path or os.environ.get('COURIER_DB_PATH') or os.path.join(app.root_path, DB_FILENAME)
    cs: str = db.connection_string
    host = (os.environ.get('COURIER_PUBLIC_HOST') or DEFAULT_PUBLIC_HOST).strip().lower()
    url = os.environ.get('COURIER_PUBLIC_URL') or f'https://{host}/api/courier/v1'
    demo = os.environ.get('COURIER_DEMO') == '1'
    store = Store(path)
    days = DayService(store, lambda car, day, window, pick, owner: erp_day.load_day(cs, car, day, window, pick, owner),
                      lambda day: routes_view(app.extensions.get(ROUTES_EXTENSION), day), demo=demo)
    app.extensions[EXTENSION_KEY] = CourierState(
        store=store, days=days, public_host=host, public_url=url, demo=demo,
        catalog_loader=lambda today: erp_day.product_catalog(cs, today),
        cars_loader=lambda today: erp_day.terminal_cars(cs, today, routes_bundle(app.extensions.get(ROUTES_EXTENSION))),
        invoice_loader=lambda day: erp_day.invoice_cars(cs, day),
        tare_links_loader=lambda: erp_day.container_links(cs),
        customer_code_loader=lambda codes: erp_day.customers_by_code(cs, codes),
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


def fleet_facts(app: Flask) -> FactsSource:
    """Трек, точки дня и заправки машин для обучения «Развоза» (контракт v1.3 §7) — после init_app:
    route_optimizer.attach_fleet_facts(app, courier.fleet_facts(app))."""
    return FactsSource(app.extensions[EXTENSION_KEY].store)


def live_facts(app: Flask) -> LiveSource:
    """Факт терминалов за день для «Մեքենաները առցանց» (№76) — после init_app:
    route_optimizer.attach_live_facts(app, courier.live_facts(app))."""
    return LiveSource(app.extensions[EXTENSION_KEY].store)


def is_public(request: Request, public_host: str) -> bool:
    """Запрос пришёл снаружи: Host — публичный хост туннеля или есть заголовок Cloudflare."""
    host = (request.host or '').split(':')[0].strip().lower()
    return host == public_host or 'Cf-Connecting-Ip' in request.headers


def is_public_request(request: Request) -> bool:
    """is_public с публичным хостом раздела (до init_app — из env)."""
    from flask import current_app
    st = current_app.extensions.get(EXTENSION_KEY)
    host = st.public_host if st is not None else (os.environ.get('COURIER_PUBLIC_HOST') or DEFAULT_PUBLIC_HOST).lower()
    return is_public(request, host)


def not_found() -> Response:
    """Ответ снаружи на закрытый путь (дашборд не виден)."""
    return Response('Not Found', status=404, mimetype='text/plain')


def public_guard(request: Request, allow: Callable[[str, str], bool] | None = None) -> Response | None:
    """Защита за туннелем: снаружи открыт только API терминалов и то, что разрешает allow(path, method)
    (app_v2: вход и журнал гаража, №53); остальное — 404 (дашборд не виден)."""
    if request.path.startswith(API_PREFIX):
        return None
    if is_public_request(request) and not (allow is not None and allow(request.path, request.method)):
        return not_found()
    return None

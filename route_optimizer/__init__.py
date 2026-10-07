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
import threading
import time
from datetime import datetime
from typing import Any

from flask import Flask

from . import erp, learning, live, live_alerts, waybill
from .actuals import YEREVAN
from .roads import RoadProvider, osm_path
from .snapshot import ResultCache, SnapshotCache, load_snapshot
from .store import Store
from .valhalla_engine import ValhallaProvider
from .views import EXTENSION_KEY, DriverGeo, RoutesState, _live_cards, bp, run_learning_job

logger = logging.getLogger(__name__)

DB_FILENAME = 'route_optimizer.db'


def init_app(app: Flask, db: Any, db_path: str | None = None) -> None:
    """Зарегистрировать Blueprint раздела.

    db — DatabaseConnection дашборда (берём только connection_string);
    db_path — файл SQLite; по умолчанию env ROUTES_DB_PATH или route_optimizer.db рядом с app_v2.py.
    Карта дорог — env ROUTES_OSM_PATH или data/roads/armenia-latest.osm.pbf (этап 5); файла нет —
    км по прямой × извилистость. Граф и матрица строятся при первом расчёте, а не здесь.
    Valhalla (план learning-loop, этап 2) — env ROUTES_ROAD_ENGINE: valhalla_time (по умолчанию: минуты машин
    менеджеров — Valhalla, км — граф OSM) | valhalla | osm; минуты грузовиков «Развоза» — модель, выбранная обучением по
    трекам водителей (до выбора — прежняя), ROUTES_TRUCK_TIME (model | valhalla), если задана, — главнее; тайлы — в
    ROUTES_VALHALLA_DIR (valhalla_engine). Тайлы собирает фоновый поток, запущенный здесь (ERP
    и SQLite он не трогает); пока не готово — граф OSM. Без pyvalhalla или карты — граф OSM, как раньше.
    """
    path = db_path or os.environ.get('ROUTES_DB_PATH') or os.path.join(app.root_path, DB_FILENAME)
    connection_string: str = db.connection_string
    roads = RoadProvider(osm_path())
    valhalla = ValhallaProvider(pbf=roads.path)
    valhalla.start()
    app.extensions[EXTENSION_KEY] = RoutesState(
        store=Store(path),
        snapshots=SnapshotCache(lambda: load_snapshot(connection_string)),
        results=ResultCache(),
        roads=roads,
        valhalla=valhalla,
        dispatch_loader=lambda since, until, day: erp.load_dispatch_data(connection_string, since, until, day),
        fact_loader=lambda day: erp.load_fact_data(connection_string, day),
        same_day_loader=lambda day: erp.load_same_day_data(connection_string, day),
        waybill_loader=lambda isns: waybill.load_lines(connection_string, isns),
        driver_list_loader=lambda since, until: waybill.load_drivers(connection_string, since, until),
        group_loader=lambda ids: erp.load_customer_groups(connection_string, ids),
        customer_ref_loader=lambda query, ids: erp.load_customer_refs(connection_string, query, ids),
        customer_hint_loader=lambda since, until: erp.load_customer_hints(connection_string, since, until),
        crew_pay_loader=lambda since, until: erp.load_crew_pay(connection_string, since, until),
    )
    app.register_blueprint(bp)
    logger.info('[Routes] Раздел «Маршруты» подключён; база настроек: %s; карта дорог: %s%s', path,
                roads.path, '' if os.path.exists(roads.path) else ' (нет — км по прямой)')


def attach_driver_geo(app: Flask, source: DriverGeo) -> None:
    """Точки и предложения водителей (раздел «Առաքիչ», driver-geo-plan.md §4) — в «Маршруты». Вызывает app_v2
    после init_app обоих разделов: пакет route_optimizer не импортирует courier. Не вызван — без точек водителей."""
    app.extensions[EXTENSION_KEY].driver_geo = source


def attach_fleet_facts(app: Flask, source: learning.FleetFacts) -> None:
    """Трек, точки дня и заправки машин (раздел «Առաքիչ», контракт v1.3 §7) — для обучения «Развоза» и отчёта «план —
    факт». Вызывает app_v2 после init_app обоих разделов. Не вызван — обучения нет, расчёты как раньше."""
    app.extensions[EXTENSION_KEY].fleet_facts = source


def attach_live_facts(app: Flask, source: live.LiveFacts) -> None:
    """Факт терминалов за день (раздел «Առաքիչ», №76) — для «Մեքենաները առցանց». Вызывает app_v2 после init_app обоих
    разделов. Не вызван — карта пуста (API отвечает 400)."""
    app.extensions[EXTENSION_KEY].live_facts = source


CATCHUP_DELAY_S = 120   # догнать пропущенный ночной прогон — через 2 мин после запуска (сервер успеет подняться)


def start_learning_scheduler(app: Flask) -> threading.Thread | None:
    """Ночное обучение (learning.NIGHTLY_AT по Еревану) — фоновый поток процесса сервера. Запускает app_v2 только при
    запуске сервера (не при импорте: тесты его не запускают); env ROUTES_LEARNING_NIGHTLY=0 — выключить. Сервер
    перезапускается при каждом автообновлении: при запуске, если прогон последнего наступившего 03:00 пропущен
    (learning.missed_run по дню последнего удачного прогона), он выполняется через CATCHUP_DELAY_S. Сбой прогона — в
    журнал, поток работает дальше; прогон идёт под тем же замком, что и кнопка «Пересчитать»."""
    if os.environ.get('ROUTES_LEARNING_NIGHTLY', '1').strip() == '0':
        logger.info('[Routes] Ночное обучение выключено (ROUTES_LEARNING_NIGHTLY=0)')
        return None
    state = app.extensions[EXTENSION_KEY]

    def run() -> None:
        try:
            with app.app_context():
                if not run_learning_job(state, datetime.now(YEREVAN).date(), 'nightly'):
                    logger.warning('[Routes] Ночное обучение пропущено: идёт другой прогон')
        except Exception:   # поток не должен умереть: следующая ночь — новая попытка
            logger.exception('[Routes] Ночное обучение: сбой')

    def loop() -> None:
        try:
            missed = learning.missed_run(datetime.now(YEREVAN), state.store.learning_last_run())
        except Exception:
            logger.exception('[Routes] Ночное обучение: не прочитан день последнего прогона')
            missed = False
        if missed:
            time.sleep(CATCHUP_DELAY_S)
            logger.info('[Routes] Ночной прогон обучения пропущен — выполняю сейчас')
            run()
        while True:
            now = datetime.now(YEREVAN)
            time.sleep(max(1.0, (learning.next_run(now) - now).total_seconds()))
            run()

    thread = threading.Thread(target=loop, name='routes-learning-nightly', daemon=True)
    thread.start()
    logger.info('[Routes] Ночное обучение «Развоза» — каждый день в %02d:%02d (Ереван)', *learning.NIGHTLY_AT)
    return thread


def start_live_alerts(app: Flask, send: Any = None, interval_s: float = live_alerts.INTERVAL_S) -> threading.Thread | None:
    """Тревоги карты машин в Telegram-группу (№76, этап 2, live_alerts) — фоновый поток процесса сервера. Запускает app_v2
    только при запуске сервера (не при импорте: тесты его не запускают). Только при ROUTES_LIVE_ALERTS=1 и заданных
    токене и чате (ROUTES_LIVE_TG_TOKEN | TELEGRAM_BOT_TOKEN, ROUTES_LIVE_TG_CHAT); иначе — None. Переменная задаётся
    только на CT115: слать должен ровно один процесс. send(text) — подмена отправки (тесты); поток каждые interval_s с
    считает карточки флота за сегодня (тот же кэш 10 с, что у карты) и шлёт новые тревоги; сбой прохода — в журнал,
    поток работает дальше. Остановка — thread.stop_event.set()."""
    config = live_alerts.config_from_env()
    if config is None:
        logger.info('[Routes] Тревоги карты в Telegram выключены (нужны %s=1, токен и чат)', live_alerts.ENABLE_ENV)
        return None
    state = app.extensions[EXTENSION_KEY]
    token, chat = config
    path = os.path.join(os.path.dirname(os.path.abspath(state.store.path)), live_alerts.STATE_FILE)

    def source() -> tuple[Any, datetime, Any] | None:
        if state.live_facts is None:
            return None
        ctx, now, _, cards = _live_cards(state, datetime.now(YEREVAN).date())
        return ctx.rules, now, cards

    alerter = live_alerts.LiveAlerter(source, send or (lambda text: live_alerts.send_telegram(token, chat, text)), path)
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                with app.app_context():
                    alerter.tick()
            except Exception:   # поток не должен умереть: следующий проход — новая попытка
                logger.exception('[Routes] Тревоги в Telegram: сбой прохода')
            stop.wait(interval_s)

    thread = threading.Thread(target=loop, name='routes-live-alerts', daemon=True)
    thread.stop_event = stop   # type: ignore[attr-defined]
    thread.start()
    logger.info('[Routes] Тревоги карты машин — в Telegram-чат, каждые %.0f с; «уже отправлено»: %s', interval_s, path)
    return thread

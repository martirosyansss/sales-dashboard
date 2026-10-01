# -*- coding: utf-8 -*-
"""Состояние раздела «Առաքիչ» в app.extensions: база, /day с кэшем, справочники ERP для офиса."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from flask import current_app

from .day import DayService
from .erp_day import CatalogItem, InvoiceCar
from .store import Store

EXTENSION_KEY = 'courier'
API_PREFIX = '/api/courier/v1/'
REF_TTL_SECONDS = 600     # справочники офиса (товары, машины) — ERP боевая


class TtlCache:
    """Значение на ключ с TTL; загрузка под lock (параллельные запросы ждут одну)."""

    def __init__(self, ttl: float = REF_TTL_SECONDS):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any, load: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            value = load()
            if len(self._data) > 32:
                self._data.clear()
            self._data[key] = (time.monotonic(), value)
            return value


@dataclass
class CourierState:
    store: Store
    days: DayService
    public_host: str
    public_url: str                          # базовый URL API в QR регистрации
    demo: bool = False
    # справочники ERP для офиса; None — ERP не подключена
    catalog_loader: Callable[[date], list[CatalogItem]] | None = None
    cars_loader: Callable[[date], list[dict[str, Any]]] | None = None
    invoice_loader: Callable[[date], tuple[list[InvoiceCar], dict[int, tuple[str, str]]]] | None = None
    refs: TtlCache = field(default_factory=TtlCache)


def state() -> CourierState:
    return current_app.extensions[EXTENSION_KEY]

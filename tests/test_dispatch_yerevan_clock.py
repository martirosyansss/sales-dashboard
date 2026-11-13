# -*- coding: utf-8 -*-
"""«Развоз»: время чтения ERP и время сборки показываются по Еревану, хотя часы сервера CT115 идут в UTC.

Запуск из корня проекта:  python -m pytest tests/test_dispatch_yerevan_clock.py -q
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from route_optimizer import erp, views  # noqa: E402
from test_route_optimizer import _dispatch_setup, _dorder, client  # noqa: E402,F401

SERVER_MOMENT = datetime(2026, 11, 9, 4, 23, 1, 500, tzinfo=timezone.utc)
YEREVAN_WALL_CLOCK = datetime(2026, 11, 9, 8, 23, 1)


class ServerOnUtc(datetime):
    """Часы сервера в UTC: без пояса now() даёт UTC, с поясом — правильный момент этого пояса."""

    @classmethod
    def now(cls, tz=None):
        return SERVER_MOMENT.astimezone(tz) if tz is not None else SERVER_MOMENT.replace(tzinfo=None)


@pytest.fixture
def empty_erp(monkeypatch):
    """ERP без данных: проверяется только отметка времени чтения."""
    monkeypatch.setattr(erp, 'datetime', ServerOnUtc)
    monkeypatch.setattr(erp, 'connect', lambda dsn: object())
    monkeypatch.setattr(erp, 'close_quietly', lambda conn: None)
    monkeypatch.setattr(erp, 'dispatch_orders', lambda conn, since, until: [])
    monkeypatch.setattr(erp, 'dispatch_orders_by_isns', lambda conn, isns: [])
    monkeypatch.setattr(erp, 'customers', lambda conn, ids: {})
    monkeypatch.setattr(erp, 'address_texts', lambda conn, ids: {})
    monkeypatch.setattr(erp, 'agent_cars', lambda conn, since, until: {})
    monkeypatch.setattr(erp, '_select', lambda conn, sql, params=(): [])


DAY = date(2026, 11, 9)


@pytest.mark.parametrize('load', [
    pytest.param(lambda: erp.load_dispatch_data('dsn', DAY, DAY, DAY), id='orders-of-the-day'),
    pytest.param(lambda: erp.load_same_day_data('dsn', DAY), id='same-day-orders'),
    pytest.param(lambda: erp.load_known_dispatch_data('dsn', ['A'], DAY), id='known-warehouse-orders'),
])
def test_erp_read_time_is_yerevan_wall_clock(empty_erp, load):
    assert load().loaded_at == YEREVAN_WALL_CLOCK


def test_build_time_is_yerevan_wall_clock(client, monkeypatch):  # noqa: F811
    _dispatch_setup(client, [_dorder(1, 101, 400.0), _dorder(2, 102, 300.0, agent=2)])
    monkeypatch.setattr(views, '_now', lambda: '2026-09-30T13:05:00')   # часы сервера, UTC
    monkeypatch.setattr(views, '_yerevan_now', lambda: datetime(2026, 9, 30, 17, 5, 0, tzinfo=views.ac.YEREVAN))
    d = client.post('/api/routes/dispatch/build', json={'date': '2026-10-01', 'trucks': ['CAR1', 'CAR2']}).get_json()
    assert d['success'] is True, d
    assert d['built_at'] == '2026-09-30T17:05:00'

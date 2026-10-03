"""Опциональная матрица Яндекс на время начала смены. Ключ остаётся на сервере.

Часовой GPS-профиль и матрица провайдера не складываются: это два источника времени.
Без ключа, при сбое, старой дате или превышении бюджета — явный запасной расчёт.
"""
from __future__ import annotations
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo
import requests

ENDPOINT = 'https://api.routing.yandex.net/v2/distancematrix'
_CACHE = {}
_LOCK = Lock()


@dataclass(frozen=True)
class ProviderMatrix:
    distances: dict = field(repr=False)
    durations: dict = field(repr=False)
    report: dict

    def km(self, a, b):
        return self.distances.get((a, b))

    def minutes(self, a, b):
        return self.durations.get((a, b))


def fetch(points, departure, key, *, get=requests.get, budget_seconds=30):
    """Блоки ≤100 элементов, только готовая полная матрица может участвовать в расчёте."""
    distances, durations = {}, {}
    started = time.monotonic()
    chunks = [points[k:k+10] for k in range(0, len(points), 10)]

    def block(origins, destinations):
        response = get(ENDPOINT, params={'apikey': key, 'origins': '|'.join(f'{a},{b}' for a,b in origins),
            'destinations': '|'.join(f'{a},{b}' for a,b in destinations), 'mode': 'driving',
            'departure_time': int(departure.timestamp())}, timeout=(3, 6), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('provider_http_error')
        rows = response.json().get('rows', [])
        if len(rows) != len(origins):
            raise ValueError('provider_incomplete')
        result = []
        for a, row in zip(origins, rows):
            elements = row.get('elements', [])
            if len(elements) != len(destinations):
                raise ValueError('provider_incomplete')
            for b, item in zip(destinations, elements):
                if a == b:
                    result.append((a,b,0.,0.)); continue
                km = item.get('distance', {}).get('value')
                sec = item.get('duration', {}).get('value')
                if (item.get('status') != 'OK' or isinstance(km, bool) or isinstance(sec, bool)
                    or not isinstance(km, (int,float)) or not isinstance(sec, (int,float))
                    or not math.isfinite(km) or not math.isfinite(sec) or km <= 0 or sec <= 0):
                    raise ValueError('provider_invalid_route')
                result.append((a,b,km/1000,sec/60))
        return result

    pool = ThreadPoolExecutor(max_workers=6)
    futures = [pool.submit(block, a, b) for a in chunks for b in chunks]
    try:
        for future in as_completed(futures, timeout=budget_seconds):
            for a,b,km,minutes in future.result():
                distances[a,b], durations[a,b] = km, minutes
            if time.monotonic()-started > budget_seconds:
                raise TimeoutError()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return ProviderMatrix(distances, durations, {'source':'yandex', 'status':'provider_forecast', 'live':False,
        'departure_at':departure.isoformat(), 'fetched_at':datetime.now(ZoneInfo('Asia/Yerevan')).isoformat(),
        'points':len(points), 'scope':'Матрица времени с прогнозом трафика на начало смены; поздние участки используют ту же матрицу'})


def load(points, day, start, now=None):
    key = os.environ.get('ROUTES_YANDEX_API_KEY', '')
    status = {'source':'yandex', 'status':'unavailable', 'live':False}
    if not key:
        return None, dict(status, reason='missing_api_key')
    now = now or datetime.now(ZoneInfo('Asia/Yerevan'))
    departure = datetime.combine(day, datetime.strptime(start, '%H:%M').time(), tzinfo=ZoneInfo('Asia/Yerevan'))
    if departure <= now:
        return None, dict(status, reason='departure_in_past')
    points = tuple(sorted(set(points)))
    try:
        limit = max(100, min(40000, int(os.environ.get('ROUTES_YANDEX_MAX_ELEMENTS', '10000'))))
    except ValueError:
        limit = 10000
    if len(points)**2 > limit:
        return None, dict(status, reason='matrix_budget', elements=len(points)**2, limit=limit)
    cache_key = points, departure, key
    with _LOCK:
        hit = _CACHE.get(cache_key)
        if hit and time.monotonic()-hit[0] < 300:
            return hit[1], hit[1].report
    try:
        matrix = fetch(points, departure, key)
    except Exception:
        # Исключения HTTP могут содержать URL с ключом: наружу и в журнал их не отдаём.
        return None, dict(status, reason='provider_error')
    with _LOCK:
        if len(_CACHE) >= 4:
            _CACHE.clear()
        _CACHE[cache_key] = time.monotonic(), matrix
    return matrix, matrix.report

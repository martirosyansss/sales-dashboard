# -*- coding: utf-8 -*-
"""Живая проверка «Առաքիչ» против боевой ERP — ТОЛЬКО ЧТЕНИЕ (не pytest: имя без test_).

Запуск из корня проекта:  python tests/courier_live_check.py [дней=5]

Для каждой машины, возившей накладные за 90 дней, и каждого из последних N рабочих дней (пн–сб, без сегодня)
собирает ответ /day тем же кодом, что и сервер (erp_day.load_day → day.day_payload), и сравнивает число точек
с фактом SALES.fDELIVERYCAR (проведённые накладные машины за дату). База courier.db — временная;
route_optimizer.db не открывается (вид на «Маршруты» пустой: без плана и склада — порядок «auto»).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app_v2  # noqa: E402  — только строка подключения к ERP (.env); сервер не запускается

from courier import erp_day  # noqa: E402
from courier.day import DayService  # noqa: E402
from courier.routes_link import RoutesView  # noqa: E402
from courier.store import Store  # noqa: E402
from route_optimizer import erp  # noqa: E402

SQL_FACT = """
SELECT LTRIM(RTRIM(s.fDELIVERYCAR)), COUNT(*), SUM(s.fTOTALSUM)
FROM SALES s WITH (NOLOCK)
WHERE s.fSTATE = 2 AND s.fDATE >= ? AND s.fDATE < ? AND LTRIM(RTRIM(ISNULL(s.fDELIVERYCAR, ''))) <> ''
GROUP BY LTRIM(RTRIM(s.fDELIVERYCAR))
"""


def workdays_back(today: date, n: int) -> list[date]:
    out, d = [], today
    while len(out) < n:
        d -= timedelta(days=1)
        if d.isoweekday() != 7:
            out.append(d)
    return sorted(out)


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cs = app_v2.db.connection_string
    today = date.today()
    days = workdays_back(today, n)
    cars = [c['code'] for c in erp_day.cars_seen(cs, today)]
    conn = erp.connect(cs)
    try:
        facts = {d: {r[0].strip(): (int(r[1]), float(r[2] or 0)) for r in erp._select(conn, SQL_FACT, (d, d + timedelta(days=1)))}
                 for d in days}
    finally:
        erp.close_quietly(conn)

    tmp = tempfile.mkdtemp(prefix='courier_live_')
    store = Store(os.path.join(tmp, 'courier.db'))
    svc = DayService(store, lambda car, d, w, pick: erp_day.load_day(cs, car, d, w, pick), lambda d: RoutesView())
    head = f"{'Дата':<11} {'Машина':<10} {'ERP':>4} {'/day':>4} {'источник':<8} {'сумма ERP':>12} {'сумма /day':>12} " \
           f"{'коорд.':>6} {'долг':>5} {'с':>5}  итог"
    print(head)
    print('-' * len(head))
    bad = 0
    for d in days:
        for car in cars:
            fact_n, fact_sum = facts[d].get(car, (0, 0.0))
            started = time.perf_counter()
            body = svc.get(car, d)
            took = time.perf_counter() - started
            stops = body['stops']
            if not stops and not fact_n:
                continue
            source = ','.join(sorted({s['source'] for s in stops})) or '—'
            total = sum(s['amount_due'] for s in stops)
            coords = sum(1 for s in stops if s['lat'] is not None)
            debt_ok = all(s['debt'] is not None for s in stops)
            ok = (len(stops) == fact_n and abs(total - fact_sum) < 0.01) if fact_n else source == 'order'
            bad += not ok
            print(f"{d.isoformat():<11} {car:<10} {fact_n:>4} {len(stops):>4} {source:<8} {fact_sum:>12,.0f} "
                  f"{total:>12,.0f} {coords:>3}/{len(stops):<2} {'да' if debt_ok else 'нет':>5} {took:>5.1f}  "
                  f"{'OK' if ok else 'РАСХОЖДЕНИЕ'}")
    print()
    print('ВСЕ СОВПАЛО' if not bad else f'РАСХОЖДЕНИЙ: {bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())

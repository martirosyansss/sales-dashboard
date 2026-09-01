# -*- coding: utf-8 -*-
"""Инварианты страницы /quantity (эндпоинт /api/managers/kpi/qty-history). READ-ONLY.

Запуск из корня проекта:  python tests/test_qty_consistency.py
Гоняет эндпоинт против БОЕВОЙ БД (только SELECT) и проверяет:
  1) регресс на зафиксированную дату 2026-08-31 (история не меняется — числа обязаны совпадать);
  2) перекрёстную сходимость: группы/каналы/территории/дни == итогам YTD/MTD до штуки.
Любая правка SQL, ломающая цифры, валит этот тест ДО того, как её увидит владелец.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_v2  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  FAIL ") + name + ((" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def call(query):
    with app_v2.app.test_request_context('/api/managers/kpi/qty-history' + query):
        resp = app_v2.api_managers_kpi_qty_history()
    body = resp[0] if isinstance(resp, tuple) else resp
    d = body.get_json()
    assert d.get('success'), "endpoint error: %s" % d
    return d


def close(a, b, tol=1.0):
    return abs(a - b) < tol


print("== Регресс на зафиксированную дату 2026-08-31 (вся компания) ==")
d = call('?filtered=0&date=2026-08-31')
mtd, ytd = d['mtd'][-1], d['ytd'][-1]
check("MTD август-2026 = 555 323.6 шт", close(mtd['qty'], 555323.6), f"got {mtd['qty']:,.1f}")
check("YTD на 31.08.2026 = 3 368 107.6 шт", close(ytd['qty'], 3368107.6), f"got {ytd['qty']:,.1f}")
check("isToday=False для прошлой даты", d['isToday'] is False)

print("== Перекрёстная сходимость (та же дата) ==")
for key, label in (("groupsAgg", "группы"), ("channels", "каналы"), ("areas", "территории")):
    for u in ("qty", "liters", "packs"):
        s = sum(x['cur'][u] for x in d[key])
        check(f"Σ {label}.{u} == YTD.{u}", close(s, ytd[u]), f"{s:,.1f} vs {ytd[u]:,.1f}")
day_sum = sum(x['qty'] for x in d['daily']['days'])
check("Σ дней == MTD (шт)", close(day_sum, mtd['qty']), f"{day_sum:,.1f} vs {mtd['qty']:,.1f}")
prod_sum = sum(p['cur']['qty'] for p in d['products'])
check("Σ товаров (топ-400) ≈ YTD (допуск 1000 шт)", close(prod_sum, ytd['qty'], tol=1000),
      f"{prod_sum:,.1f} vs {ytd['qty']:,.1f}")

print("== Регресс фильтров (та же дата) ==")
d1 = call('?filtered=1&date=2026-08-31')
check("MTD с KPI-фильтрами = 268 147 шт", close(d1['mtd'][-1]['qty'], 268147.0),
      f"got {d1['mtd'][-1]['qty']:,.1f}")
d2 = call('?filtered=0&groups=20&date=2026-08-31')
check("MTD группа «20 Ջրեղեն» = 312 294 шт", close(d2['mtd'][-1]['qty'], 312294.0),
      f"got {d2['mtd'][-1]['qty']:,.1f}")

print("== Санитарные проверки текущего дня ==")
dn = call('?filtered=0')
check("сегодня isToday=True", dn['isToday'] is True)
check("11 лет в рядах", len(dn['mtd']) == 11 and len(dn['ytd']) == 11)
check("48 месяцев в monthly", len(dn['monthly']) == 48)
check("охват литров > 90%", dn['coverage']['liters'] > 90, f"{dn['coverage']}")

print()
if FAILS:
    print("ПРОВАЛЕНО: %d проверок: %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")

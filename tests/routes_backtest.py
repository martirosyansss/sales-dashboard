# -*- coding: utf-8 -*-
"""Живая приёмка этапа 1 «Маршрутов» на боевой БД — ТОЛЬКО ЧТЕНИЕ (план этапа 1, §8).

Запуск из корня проекта:  python tests/routes_backtest.py
Имя без префикса test_: pytest его не собирает (нужна боевая ERP).

Модель прогоняется на 4 прошлых неделях пн–сб и сверяется с фактом:
  - выручка: у каждого включённого менеджера |model_week / fact_in_plan − 1| ≤ 10%;
  - км:      у каждого менеджера |Σ road_km / Σ track_km − 1| ≤ 15% (этап 5: км по дорогам).
Цифры не подгоняются: если критерий не выполнен, отчёт так и говорит. Код выхода: 0 — всё
пройдено, 1 — нет.

Определения (утечки тестовых данных нет — спрос, сезонная поправка и извилистость берутся ДО
тестовых недель; состав плана — шаблоны и менеджеры в расчёте — текущий, как в разделе):
  - тестовые недели: 4 недели пн–сб, последняя кончается в последнюю субботу перед сегодня
    (если до неё меньше 3 дней — неделей раньше: заказы субботы отгружаются в пн–вт);
    воскресные заказы и воскресные дни плана в сверку не входят;
  - окно оценки спроса: 12 недель перед тестом; λ = заказы / (экспозиция / 7) × scale;
  - scale = (выручка компании пн–сб за тестовые недели год назад / 4) /
            (выручка пн–сб за окно оценки год назад / 12) — сдвиг на 364 дня (те же дни недели);
  - менеджеры в сверке — те же, что в разделе сегодня: выбор «В расчёте» из настроек, а у кого
    записи нет — только шаблоны с работой (≥ 1 заказ или визит) за последние 8 недель. Как и сами
    шаблоны (берутся текущие), это конфигурация плана, а не оценка спроса;
  - model_week = Σ по визитам плана пн–сб p × средний заказ, p = min(1, λ / f), где f — визиты
    клиента в неделю только у менеджеров в сверке (к клиенту никто из них не ездит — у всех);
  - fact_in_plan — средняя недельная выручка заказов этого агента от клиентов его плана пн–сб;
    fact_total — вся выручка агента; заказ = документы клиента за дату заказа, агент заказа —
    агент самого крупного документа;
  - км: на тестовых днях (≥ 8 визитов с GPS, ≥ 30 точек трека) road_km = км по дорогам карты
    OpenStreetMap между точками визитов по fSTARTTIME (route_optimizer.roads; точка дальше 0,5 км
    от дороги — участок по прямой × detour), track_km — км трека между первым и последним визитом.
    Для сравнения — прежняя модель этапа 1: км по прямой между теми же точками × detour, где
    detour = медиана (км трека / км по прямой) по дням 6 недель перед тестом. Кэш расстояний
    бэктеста — отдельный файл рядом с картой (<карта>.backtest.npz): GPS-точки визитов не
    смешиваются с точками плана;
  - время дня (третий блок, справочно — в ИТОГ не входит): по тем же 6 неделям калибруются
    скорости движения и средняя стоянка на визит (как в разделе), длительности по классам — по
    долям классов в плане пн–сб менеджеров в сверке (класс — по среднему кг заказа в окне оценки);
    на тестовых днях с ≥ 8 визитами с GPS и ≥ 30 точками трека модель = Σ длительностей всех
    визитов дня + дорога между точками визитов по fSTARTTIME (км по дорогам / скорость
    город/область), факт — работа по треку того же дня (как в разделе): окно «первый fSTARTTIME →
    конец последнего визита» без пауз — разрывов трека > 15 мин; отметка — ±20% по менеджеру.
    Справочно — окно целиком и доля пауз в нём.
"""
from __future__ import annotations

import os
import sys
import time as _time
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import fmean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app_v2  # noqa: E402  — только строка подключения к ERP; сервер не запускается
from route_optimizer import demand as dm  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import evaluate as ev  # noqa: E402
from route_optimizer import plan as pl  # noqa: E402
from route_optimizer import roads as rd  # noqa: E402
from route_optimizer.store import DEFAULT_SETTINGS, Store  # noqa: E402

REV_TOLERANCE = 0.10
KM_TOLERANCE = 0.15      # этап 5: км по дорогам
TIME_TOLERANCE = 0.20    # справочно: в ИТОГ не входит
TEST_WEEKS = 4
EST_WEEKS = 12
CALIB_WEEKS = 6
MIN_LAG_DAYS = 3
YEAR_SHIFT = timedelta(days=364)
WORKDAYS = range(1, 7)   # пн–сб (plan: 1 = пн)


def test_period(today: date) -> tuple[date, date]:
    """[понедельник первой тестовой недели, воскресенье после последней) — полуинтервал."""
    last_sat = today - timedelta(days=(today.weekday() - 5) % 7 or 7)
    if (today - last_sat).days < MIN_LAG_DAYS:
        last_sat -= timedelta(days=7)
    return last_sat - timedelta(days=7 * TEST_WEEKS - 2), last_sat + timedelta(days=1)


def is_test_day(d: date, start: date, end: date) -> bool:
    return start <= d < end and d.weekday() < 6   # без воскресений


def routes_db_path() -> str:
    return os.environ.get('ROUTES_DB_PATH') or os.path.join(ROOT, 'route_optimizer.db')


def included_agents(plan: pl.CurrentPlan, active: frozenset[int]) -> list[int]:
    """Включённые менеджеры — как в разделе (Bundle.included): явный выбор из настроек, если база
    уже есть, иначе («авто») — есть ли у агента работа (active)."""
    path = routes_db_path()
    if not os.path.exists(path):   # базу не создаём: бэктест ничего не пишет
        return [a for a in plan.agent_ids if a in active]
    bundle = Store(path).load()
    return [a for a in plan.agent_ids if bundle.included(a, active)]


def settings_now() -> dict:
    """Настройки раздела (город, пороги размера, сети), если база уже есть, иначе — по умолчанию."""
    path = routes_db_path()
    return Store(path).load().settings if os.path.exists(path) else dict(DEFAULT_SETTINGS)


def pct(x: float | None) -> str:
    return '—' if x is None else f'{x * 100:+.1f}%'


def num(x: float | None, digits: int = 1) -> str:
    return 'нет данных' if x is None else f'{x:.{digits}f}'


def money(x: float) -> str:
    return f'{x:,.0f}'.replace(',', ' ')


def main() -> int:
    today = date.today()
    test_start, test_end = test_period(today)
    est_start = test_start - timedelta(weeks=EST_WEEKS)
    calib_start = test_start - timedelta(weeks=CALIB_WEEKS)
    active_start = today - timedelta(days=ev.ACTIVE_WINDOW_DAYS)   # как Snapshot.active_agents
    s = settings_now()
    chain = set(s['chain_groups'])

    conn = erp.connect(app_v2.db.connection_string)
    try:
        agents = erp.agents(conn)
        plan = pl.build_plan(erp.route_templates(conn))
        docs = erp.sales_docs(conn, est_start - YEAR_SHIFT - timedelta(days=14),
                              today + timedelta(days=1))
        first_sale = erp.first_sale_dates(conn, plan.customer_ids)
        visits = erp.visits(conn, min(calib_start, active_start), today)
        fixes = erp.tracks(conn, datetime.combine(calib_start, time.min),
                           datetime.combine(test_end, time.min))
        # группы клиентов нужны только для сетей (всегда «крупный»)
        customers = (erp.customers(conn, sorted(set(plan.customer_ids)
                                                | {v.customer_id for v in visits}))
                     if chain else {})
    finally:
        erp.close_quietly(conn)

    orders = dm.group_orders(docs)

    # --- сезонная поправка λ по прошлому году (пн–сб в обоих окнах, как и факт) ---
    def company_revenue(start: date, end: date) -> float:
        return sum(o.revenue for o in orders if is_test_day(o.date, start, end))

    rev_test_ly = company_revenue(test_start - YEAR_SHIFT, test_end - YEAR_SHIFT)
    rev_est_ly = company_revenue(est_start - YEAR_SHIFT, test_start - YEAR_SHIFT)
    scale_note = ''
    if rev_test_ly > 0 and rev_est_ly > 0:
        scale = (rev_test_ly / TEST_WEEKS) / (rev_est_ly / EST_WEEKS)
    else:
        scale = 1.0
        scale_note = ' — НЕТ ДАННЫХ за прошлый год, поправка не применяется'

    # --- спрос клиентов плана по окну оценки ---
    est_window = [(est_start, test_start)]
    by_customer: dict[int, list[dm.Order]] = defaultdict(list)
    for o in orders:
        if est_start <= o.date < test_start:
            by_customer[o.customer_id].append(o)
    demand: dict[int, dm.Demand] = {}
    for cid in plan.customer_ids:
        own = by_customer.get(cid, [])
        firsts = [d for d in (first_sale.get(cid), own[0].date if own else None) if d]
        demand[cid] = dm.window_demand(own, est_window, min(firsts) if firsts else None,
                                       test_start, scale=scale)

    # --- менеджеры в сверке — как в разделе сегодня: настройки, иначе — работа за 8 недель ---
    active = ev.active_agents(orders, visits, active_start, today)
    included = included_agents(plan, active)
    freq = plan.visits_per_week_among(included)   # f_i — только визиты включённых менеджеров

    # --- выручка: модель и факт по менеджерам ---
    cycle = plan.cycle_weeks
    rows = []
    for agent_id in included:
        days = [d for d in plan.days_of(agent_id) if d.weekday in WORKDAYS]
        plan_customers = {v.customer_id for d in days for v in d.visits}
        model = 0.0
        capped = 0.0
        for d in days:
            for v in d.visits:
                dem = demand[v.customer_id]
                p = dm.visit_probability(dem.lam, freq[v.customer_id])
                model += p * dem.mean_revenue / cycle
                if p >= 1.0:
                    capped += dem.mean_revenue / cycle
        test_orders = [o for o in orders if o.agent_id == agent_id
                       and is_test_day(o.date, test_start, test_end)]
        fact_total = sum(o.revenue for o in test_orders) / TEST_WEEKS
        fact_in_plan = sum(o.revenue for o in test_orders
                           if o.customer_id in plan_customers) / TEST_WEEKS
        new_fact = sum(o.revenue for o in test_orders if o.customer_id in plan_customers
                       and not by_customer.get(o.customer_id)) / TEST_WEEKS
        rows.append(dict(agent_id=agent_id, days=len(days), model=model, capped=capped,
                         fact_in_plan=fact_in_plan, fact_total=fact_total, new_fact=new_fact))

    # --- км: калибровка извилистости и сверка с треком ---
    day_fixes = ev.fixes_by_day(fixes)
    calib_days = ev.day_tracks(visits, day_fixes, calib_start, test_start)
    detour, detour_days = ev.calibrate_detour(calib_days)
    test_days = [d for d in ev.day_tracks(visits, day_fixes, test_start, test_end)
                 if d.day.weekday() < 6]
    km_by_agent: dict[int, list[ev.DayTrack]] = defaultdict(list)
    for d in test_days:
        km_by_agent[d.agent_id].append(d)

    # --- время дня (справочно): калибровка по тем же 6 неделям до теста, как в разделе ---
    center = (float(s['city_center_lat']), float(s['city_center_lon']))
    speed_city, speed_region = ev.calibrate_speeds(calib_days, center, float(s['city_radius_km']))
    visit_avg = ev.calibrate_visit_minutes(calib_days)
    calib = ev.Calibration(detour, speed_city, speed_region, detour_days, visit_avg)
    road = ev.road_norms({}, calib)                        # только калибровка, без ручных чисел

    # --- км по дорогам: те же точки визитов тестовых дней по fSTARTTIME (этап 5) ---
    test_visits: dict[tuple[int, date], list[ev.ActualVisit]] = defaultdict(list)
    for v in visits:
        if is_test_day(v.day, test_start, test_end):
            test_visits[(v.agent_id, v.day)].append(v)

    def day_points(d: ev.DayTrack) -> list[tuple[float, float]]:
        """Точки визитов дня с GPS по fSTARTTIME — как в day_tracks."""
        vs = sorted((v for v in test_visits[(d.agent_id, d.day)] if ev.visit_point_ok(v)),
                    key=lambda v: v.start)
        return [(v.lat, v.lon) for v in vs]

    roads = rd.open_roads(cache_name='backtest')
    all_points = [p for d in test_days for p in day_points(d)]
    roads_seconds = 0.0
    if roads is not None:
        started = _time.perf_counter()
        roads.ensure(all_points)
        roads_seconds = _time.perf_counter() - started
        if roads.failed:
            roads = None
    norms = ev.Norms.from_settings(dict(s, **dict.fromkeys(ev.ROAD_NORMS)), calib, roads)
    road_km = {(d.agent_id, d.day): sum(norms.km(a, b) for a, b in zip(pts, pts[1:]))
               for d in test_days for pts in [day_points(d)]}

    def size_of(cid: int) -> str:
        own = by_customer.get(cid)                         # заказы в окне оценки спроса
        customer = customers.get(cid)
        return dm.size_class(fmean(o.kg for o in own) if own else None,
                             customer.group if customer else None, chain,
                             s['size_small_max_kg'], s['size_medium_max_kg'])

    shares = ev.size_shares(plan, {c: size_of(c) for c in plan.customer_ids}, included, WORKDAYS)
    visit = ev.visit_norms({}, visit_avg, shares)
    visit_minutes = {size: visit[f'visit_min_{size}'][0] for size in ev.VISIT_NORMS}
    included_set = set(included)
    day_visits: dict[tuple[int, date], list[ev.ActualVisit]] = defaultdict(list)
    for v in visits:
        if v.agent_id in included_set and is_test_day(v.day, test_start, test_end):
            day_visits[(v.agent_id, v.day)].append(v)
    # (модель, работа по треку, окно), мин — тестовые дни с ≥ 8 визитами с GPS и ≥ 30 точками трека
    time_by_agent: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for d in test_days:
        vs = day_visits.get((d.agent_id, d.day))
        if not vs:                                     # менеджер не в сверке
            continue
        located = sorted((v for v in vs if ev.visit_point_ok(v)), key=lambda v: v.start)
        _, drive = ev.route_metrics([(v.lat, v.lon) for v in located], None, norms)
        start, end = ev.visit_span(vs)
        work, pauses = ev.work_pause_minutes(d.fixes, start, end)
        model_min = sum(visit_minutes[size_of(v.customer_id)] for v in vs) + drive
        time_by_agent[d.agent_id].append((model_min, work, work + pauses))

    # --- отчёт ---
    print('Бэктест этапа 1 «Маршруты» (ERP только чтение)')
    print(f'  сегодня {today}; тестовые недели пн–сб: {test_start} … {test_end - timedelta(days=1)}')
    print(f'  окно оценки спроса: {est_start} … {test_start - timedelta(days=1)} ({EST_WEEKS} нед.)')
    print(f'  scale = ({money(rev_test_ly)} / {TEST_WEEKS}) / ({money(rev_est_ly)} / {EST_WEEKS}) '
          f'= {scale:.3f}  (выручка компании пн–сб год назад){scale_note}')
    detour_txt = f'{detour:.3f}' if detour is not None else 'нет данных'
    print(f'  извилистость (калибровка {calib_start} … {test_start - timedelta(days=1)}): '
          f'{detour_txt} по {detour_days} дн.')
    if roads is None:
        print(f'  КАРТЫ ДОРОГ НЕТ ({rd.osm_path()}) — км модели по прямой × извилистость')
    else:
        n_points = len({rd.point_key(p) for p in all_points})
        print(f'  км по дорогам: точек визитов {n_points}, дальше 0,5 км от дороги '
              f'{roads.unsnapped(all_points)} (для них участки по прямой × {norms.detour:g}); '
              f'расчёт расстояний {roads_seconds:.0f} с')
    print(f'  цикл плана: {cycle} нед.; менеджеров в сверке: {len(rows)}')
    left_out = [a for a in plan.agent_ids if a not in included]
    if left_out:
        print('  не в сверке: ' + ', '.join(
            (agents[a].code if a in agents else str(a))
            + (' (выключен в настройках)' if a in active else ' (нет заказов и визитов за 8 нед.)')
            for a in left_out))
    print()
    header = (f'{"Менеджер":<9} {"model_week":>11} {"fact_in_plan":>12} {"fact_total":>11} '
              f'{"вне плана":>9} {"откл.":>7} {"выручка":<12} {"дн.км":>5} {"road_km":>9} '
              f'{"track_km":>9} {"откл.":>7} {"км ±" + format(KM_TOLERANCE, ".0%"):<12} '
              f'{"прям×изв":>9} {"откл.":>7}')
    print(header)
    print('-' * len(header))

    def state(ok: bool, dev: float | None) -> str:
        return 'ПРОЙДЕНО' if ok else ('НЕТ ДАННЫХ' if dev is None else 'НЕ ПРОЙДЕНО')

    results = []
    not_applicable = []
    for r in rows:
        code = agents[r['agent_id']].code if r['agent_id'] in agents else str(r['agent_id'])
        if r['days'] == 0:
            not_applicable.append(code)
            print(f'{code:<9} нет дней пн–сб в плане (только воскресенье) — критерии неприменимы')
            continue
        rev_dev = r['model'] / r['fact_in_plan'] - 1 if r['fact_in_plan'] > 0 else None
        rev_ok = rev_dev is not None and abs(rev_dev) <= REV_TOLERANCE
        outside = 1 - r['fact_in_plan'] / r['fact_total'] if r['fact_total'] > 0 else None
        kd = km_by_agent.get(r['agent_id'], [])
        track_km = sum(d.track_km for d in kd)
        model_km = sum(road_km[(d.agent_id, d.day)] for d in kd) if kd else None
        km_dev = model_km / track_km - 1 if model_km is not None and track_km > 0 else None
        km_ok = km_dev is not None and abs(km_dev) <= KM_TOLERANCE
        # прежняя модель этапа 1 — для сравнения, в ИТОГ не входит
        old_km = sum(d.straight_km for d in kd) * detour if kd and detour else None
        old_dev = old_km / track_km - 1 if old_km is not None and track_km > 0 else None
        results.append((code, r, rev_ok, km_ok))
        print(f'{code:<9} {money(r["model"]):>11} {money(r["fact_in_plan"]):>12} '
              f'{money(r["fact_total"]):>11} '
              f'{(f"{outside * 100:.1f}%" if outside is not None else "—"):>9} '
              f'{pct(rev_dev):>7} {state(rev_ok, rev_dev):<12} {len(kd):>5} '
              f'{(f"{model_km:.1f}" if model_km is not None else "—"):>9} '
              f'{(f"{track_km:.1f}" if kd else "—"):>9} {pct(km_dev):>7} {state(km_ok, km_dev):<12} '
              f'{(f"{old_km:.1f}" if old_km is not None else "—"):>9} {pct(old_dev):>7}')

    kd_all = [d for _, r, _, _ in results for d in km_by_agent.get(r['agent_id'], [])]
    track_all = sum(d.track_km for d in kd_all)
    if track_all > 0:
        road_all = sum(road_km[(d.agent_id, d.day)] for d in kd_all)
        old_all = sum(d.straight_km for d in kd_all) * detour if detour else None
        print(f'Км всего за {len(kd_all)} дн.: трек {track_all:.1f}; по дорогам {road_all:.1f} '
              f'({pct(road_all / track_all - 1)}); по прямой × извилистость '
              + (f'{old_all:.1f} ({pct(old_all / track_all - 1)})' if old_all is not None else '—'))

    print()
    print('Диагностика выручки (в неделю):')
    for code, r, _, _ in results:
        print(f'  {code:<9} модель у клиентов с p = 1 (λ ≥ визитов): {money(r["capped"])}; '
              f'факт от клиентов плана без заказов в окне оценки: {money(r["new_fact"])}')
    print()

    print(f'Время дня у клиентов — справочно, в ИТОГ не входит (дни пн–сб с ≥ {ev.CALIB_MIN_VISITS} '
          f'визитами с GPS и ≥ {ev.CALIB_MIN_FIXES} точками трека):')
    print(f'  калибровка {calib_start} … {test_start - timedelta(days=1)}: стоянка на визит по GPS '
          f'{num(visit_avg)} мин → мелкий / средний / крупный '
          + ' / '.join(f'{visit_minutes[z]:g}' for z in ev.VISIT_NORMS)
          + ' мин (' + ('по GPS' if visit_avg is not None else 'нет калибровки — по умолчанию')
          + '; доли визитов плана ' + ' / '.join(f'{shares[z]:.0%}' for z in ev.VISIT_NORMS) + ')')
    print(f'  скорость движения: город {num(speed_city)}, область {num(speed_region)} км/ч '
          f'(в модели {road["speed_city_kmh"][0]:g} / {road["speed_region_kmh"][0]:g}); '
          f'извилистость {road["detour_factor"][0]:g}')
    print('  модель = Σ длительностей визитов дня + дорога между ними (км по дорогам / '
          'скорость); работа = езда и стоянки по треку в окне «первый визит → конец последнего», '
          'без пауз (разрывов трека > 15 мин); окно и доля пауз — справочно')
    header = (f'{"Менеджер":<9} {"дн.":>4} {"модель, ч/день":>14} {"работа GPS, ч/день":>18} '
              f'{"откл.":>7} {"±" + format(TIME_TOLERANCE, ".0%"):<12} {"окно, ч/день":>12} '
              f'{"паузы":>6}')
    print(header)
    print('-' * len(header))
    time_ok = time_n = 0
    for code, r, _, _ in results:
        tdays = time_by_agent.get(r['agent_id'], [])
        n = len(tdays)
        model_min = sum(m for m, _, _ in tdays)
        work_min = sum(w for _, w, _ in tdays)
        span_min = sum(s for _, _, s in tdays)
        time_dev = model_min / work_min - 1 if work_min > 0 else None
        if time_dev is None:
            print(f'{code:<9} {n:>4} {"—":>14} {"—":>18} {"—":>7} {"НЕТ ДАННЫХ":<12} {"—":>12} {"—":>6}')
            continue
        time_in = abs(time_dev) <= TIME_TOLERANCE
        time_n += 1
        time_ok += time_in
        print(f'{code:<9} {n:>4} {model_min / n / 60:>14.1f} {work_min / n / 60:>18.1f} '
              f'{pct(time_dev):>7} {("в пределах" if time_in else "ВНЕ"):<12} '
              f'{span_min / n / 60:>12.1f} {1 - work_min / span_min:>6.0%}')
    print(f'Время ±{TIME_TOLERANCE:.0%} (справочно): в пределах у {time_ok} из {time_n}')
    print()
    evaluable = len(results)
    passed = sum(1 for *_, rev_ok, km_ok in results if rev_ok and km_ok)
    print(f'Выручка ±{REV_TOLERANCE:.0%}: пройдено у {sum(1 for r in results if r[2])} из {evaluable}')
    print(f'Км ±{KM_TOLERANCE:.0%}: пройдено у {sum(1 for r in results if r[3])} из {evaluable}')
    if not_applicable:
        print(f'Неприменимо (нет плана пн–сб): {", ".join(not_applicable)}')
    verdict = 'ПРОЙДЕНО' if evaluable and passed == evaluable else 'НЕ ПРОЙДЕНО'
    print(f'ИТОГ: {verdict} по {passed} из {evaluable}')
    return 0 if verdict == 'ПРОЙДЕНО' else 1


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Живая приёмка этапов 3 и 4 «Маршрутов» (режимы А и Б) на боевой БД — ТОЛЬКО ЧТЕНИЕ
(план этапа 3, §12; план этапа 4, §7).

Запуск из корня проекта:  python tests/routes_optimize_check.py
Имя без префикса test_: pytest его не собирает (нужна боевая ERP).

ERP читается одним снимком этапа 1 (erp._select, только SELECT). Настройки и решения владельца —
из КОПИИ route_optimizer.db (или ROUTES_DB_PATH) во временной папке: сама база не меняется (даже
миграцией схемы); базы нет — настройки по умолчанию. Расчёт — как кнопка «Рассчитать» по умолчанию:
все менеджеры в расчёте, старт «текущий план», частота «по продажам».

Критерии (цифры не подгоняются: если критерий не выполнен, отчёт так и говорит):
  - частоты: у каждого клиента визитов за цикл = целевая частота × 2;
  - шаблоны: все допустимые, закреплённые не тронуты, запрещённые не выбраны;
  - время: нет дней «стало» с планом длиннее окна, если у менеджера в «было» таких не было;
    если были — в неделю их не больше;
  - стоимость: C_стало ≤ C_было компании — менеджеры + дизель парка (быстрая оценка; парк общий,
    поэтому сравнение — по компании, а у менеджеров — справочно); итог «стало» не хуже по слабым дням и
    км менеджеров (полная оценка этапа 1) — если хуже, отчёт показывает, где и почему;
  - выручка: снижение частоты не уменьшает выручку модели ни в один сезон (зима, лето, год) — ни
    по компании, ни у менеджера (частота по продажам — по самому высокому спросу из сезонов);
  - скорость: весь расчёт ≤ 2 мин при optimizer_seconds_per_manager = 8;
  - повторяемость: второй запуск с теми же параметрами даёт те же шаблоны;
  - статус клиента (§15): у потерянных и без заказов за год — предложение «убрать из маршрута», у
    затихших частота «стало» ≤ 0.5 (кроме решений владельца); выручка «было/стало» — без них (у
    затихших, потерянных и без заказов λ = 0). Сводка по статусам — отдельной строкой.
Режим Б (передача магазинов между менеджерами, этап 4) — тот же снимок и настройки:
  - ограничения режима А (частоты, шаблоны, время) соблюдены и у переданных клиентов;
  - стоимость компании ≤ режима А (старт поиска — итог режима А);
  - закреплённые и клиенты нескольких менеджеров не передаются;
  - балансы сходятся: отдано = получено (магазины, выручка в месяц, долг), у менеджера «отдаёт» = сумма
    его передач;
  - повторяемость; весь расчёт ≤ 2 мин;
  - ключевой показатель — слабые зимние дни A002/10 и A004/11 в режиме Б против режима А.
Парк машин (fleet-plan §5): дизель парка «было → стало» (режимы А и Б), рейсов в день, загрузка, тоннаж и
рабочий день машин соблюдены (рейсы пересчитываются заново по пробам «стало»); сверка «как сейчас» с
фактом ERP за 4 недели (SALES.fDELIVERYCAR, только SELECT) — справочно.
ROUTES_TEST_TRUCKS="код:кг:л100,…" — тестовые машины только в КОПИИ базы (остальные машины в копии
выключаются); рабочая база не меняется.
Код выхода: 0 — все критерии выполнены, 1 — нет.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from statistics import fmean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app_v2  # noqa: E402  — только строка подключения к ERP; сервер не запускается
from route_optimizer import demand as dm  # noqa: E402
from route_optimizer import erp  # noqa: E402
from route_optimizer import evaluate as ev  # noqa: E402
from route_optimizer import fleet as fl  # noqa: E402
from route_optimizer import optimize as opt  # noqa: E402
from route_optimizer import patterns as pt  # noqa: E402
from route_optimizer import status as cst  # noqa: E402
from route_optimizer.snapshot import load_snapshot  # noqa: E402
from route_optimizer.store import DEFAULT_SETTINGS, Bundle, Changes, Store, Truck  # noqa: E402

TIME_LIMIT_S = 120.0
SECONDS_PER_MANAGER = 8
PARAMS = {'agent_ids': None, 'start': 'current', 'frequencies': 'sales'}


def load_settings() -> tuple[Bundle, list, str]:
    """(настройки, решения, откуда) — из копии базы маршрутов; сама база не открывается на запись.
    ROUTES_TEST_TRUCKS — тестовые машины: пишутся только в копию, остальные машины копии выключаются."""
    path = os.environ.get('ROUTES_DB_PATH') or os.path.join(ROOT, 'route_optimizer.db')
    if not os.path.exists(path):
        return Bundle(dict(DEFAULT_SETTINGS), None, {}, {}), [], 'по умолчанию (базы маршрутов нет)'
    folder = tempfile.mkdtemp(prefix='routes_check_')
    copy = os.path.join(folder, 'routes.db')
    for suffix in ('', '-wal', '-shm'):
        if os.path.exists(path + suffix):
            shutil.copy2(path + suffix, copy + suffix)
    try:
        store = Store(copy)
        source = f'копия {path}'
        spec = os.environ.get('ROUTES_TEST_TRUCKS')
        if spec:
            bundle = store.load()
            test = {}
            for item in spec.split(','):
                code, cap, l100 = item.rsplit(':', 2)
                test[code.strip()] = Truck(code.strip(), float(cap), float(l100), None, True)
            trucks = [test.get(c) or replace(t, active=False) for c, t in bundle.trucks.items()]
            trucks += [t for c, t in test.items() if c not in bundle.trucks]
            store.save(Changes(bundle.settings, False, None, tuple(trucks), ()), 'routes_optimize_check')
            source += ' + тестовые машины ' + ', '.join(f'{c} {t.capacity_kg / 1000:g} т / {t.fuel_l_per_100km:g} л'
                                                       for c, t in test.items()) + ' (только в копии)'
        return store.load(), store.load_decisions(), source
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def num(x: float | None, digits: int = 1) -> str:
    if x is None:
        return '—'
    return f'{x:,.{digits}f}'.replace(',', ' ')


def arrow(a: float | None, b: float | None, digits: int = 1) -> str:
    return f'{num(a, digits)} → {num(b, digits)}'


def status(ok: bool) -> str:
    return 'ПРОЙДЕНО' if ok else 'НЕ ПРОЙДЕНО'


def overtime_days(days: list[dict], window: float) -> int:
    return sum(1 for d in days if d['plan_minutes'] is not None and d['plan_minutes'] > window)


def main() -> int:
    bundle, decisions, source = load_settings()
    settings = dict(bundle.settings, optimizer_seconds_per_manager=SECONDS_PER_MANAGER)
    bundle = Bundle(settings, bundle.depot, bundle.trucks, bundle.managers)
    started = time.perf_counter()
    snap = load_snapshot(app_v2.db.connection_string)
    snap_seconds = time.perf_counter() - started
    center = (float(settings['city_center_lat']), float(settings['city_center_lon']))
    calib = ev.calibrate(snap.recent_visits, snap.fixes_by_agent,
                         snap.today - timedelta(days=ev.CALIB_WINDOW_DAYS), snap.today, center,
                         float(settings['city_radius_km']))

    runs = []
    for _ in range(2):
        t0 = time.perf_counter()
        out = opt.run_optimization(snap, bundle, calib, decisions, PARAMS)
        runs.append((out, time.perf_counter() - t0))
    out, run_seconds = runs[0]
    res = out.result
    window = out.before.norms.work_minutes

    print('Приёмка этапа 3 «Маршруты» — режим А (ERP только чтение)')
    print(f'  снимок ERP на {snap.data_as_of:%d.%m.%Y %H:%M} ({snap_seconds:.1f} с); настройки: {source}; '
          f'решений владельца: {len(decisions)}')
    print(f'  параметры: все менеджеры в расчёте, старт «текущий план», частота «по продажам», '
          f'{SECONDS_PER_MANAGER} с на менеджера; цена топлива {res["fuel_price_used"]} драм/л '
          f'({"настройки" if res["fuel_price_source"] == "settings" else "по умолчанию"}); '
          f'дизель парка в стоимости: {"да" if res["truck_costs"] else "нет"}')
    trucks, incomplete = fl.fleet_trucks(bundle.trucks, {c: car.name for c, car in snap.cars.items()})
    print(f'  парк: {", ".join(f"{t.car_code} ({t.name}) {t.capacity_kg / 1000:g} т, {t.l100:g} л/100 км" for t in trucks) or "нет"}'
          f'; склад {bundle.depot}; без тоннажа или расхода (не в расчёте): {len(incomplete)}')
    print()

    # --- таблица «было → стало» ---
    header = (f'{"Менеджер":<9} {"визитов/нед":>15} {"слабых дн./нед":>15} {"км/нед":>17} '
              f'{"ч у клиентов":>13} {"C, драм/нед":>19} {"перен.":>6} {"частота":>7} {"оба":>4} '
              f'{"убрать":>6} {"поиск, с":>8}')
    print(header)
    print('-' * len(header))
    by_agent = {o.agent_id: o for o in out.managers}
    for m in res['managers']:
        b, a = m['before'], m['after']
        kinds = Counter(ch['type'] for ch in m['changes'])
        o = by_agent[m['agent_id']]
        print(f'{m["code"]:<9} {arrow(b["visits"], a["visits"]):>15} '
              f'{arrow(b["days_below_min"], a["days_below_min"]):>15} '
              f'{arrow(b["manager_km"], a["manager_km"]):>17} '
              f'{arrow(b["avg_work_hours"], a["avg_work_hours"]):>13} '
              f'{arrow(b["cost"], a["cost"], 0):>19} {kinds["move"]:>6} {kinds["frequency"]:>7} '
              f'{kinds["both"]:>4} {kinds["remove"]:>6} {o.stats.seconds:>8.2f}'
              + ('  СТОП ПО ВРЕМЕНИ' if m['time_capped'] else ''))
    tb, ta = res['before'], res['after']
    print('-' * len(header))
    print(f'{"Компания":<9} {arrow(tb["visits_week"], ta["visits_week"]):>15} '
          f'{arrow(tb["days_below_min"], ta["days_below_min"]):>15} '
          f'{arrow(tb["manager_km_week"], ta["manager_km_week"]):>17} '
          f'{arrow(tb["avg_plan_work_hours"], ta["avg_plan_work_hours"]):>13}')
    print(f'  выручка в неделю (модель): зима {arrow(tb["revenue_week_low"], ta["revenue_week_low"], 0)}, '
          f'лето {arrow(tb["revenue_week_peak"], ta["revenue_week_peak"], 0)}, '
          f'год {arrow(tb["revenue_week_year"], ta["revenue_week_year"], 0)}')
    print(f'  рабочих дней в неделю {arrow(tb["days_total"], ta["days_total"])}; '
          f'км грузовиков {arrow(tb["truck_km_week"], ta["truck_km_week"])}; '
          f'устаревших решений (не применены): {len(res["stale_decisions"])}')
    for m in res['managers']:
        f = m['feasibility']
        if not f['reachable']:
            print(f'  {m["code"]}: зимой {num(f["revenue_week_low"], 0)} драм/нед → 100 000 возможно '
                  f'максимум в {f["max_days_ge_min"]} из {f["workdays"]} дней')
    print()

    # --- статус клиента (§15): сводка ---
    models = out.before.models
    statuses = {c: models[c].status for c in sorted({c for o in out.managers for c in o.customers})}
    counts = Counter(s.status for s in statuses.values() if s is not None)
    risk = tb['at_risk']
    silent = [c for c, s in statuses.items() if s is not None and s.silent]
    # сколько затихшие, потерянные и без заказов давали бы в неделю по истории заказов — в «было» не входит
    hist_week = sum(dm.visit_probability(models[c].year_hist.lam, models[c].visits_per_week)
                    * models[c].visits_per_week * models[c].year_hist.mean_revenue for c in silent)
    labels = (('active', 'активных'), ('new', 'новых'), ('seasonal', 'сезонных'))
    print(f'Статус клиентов менеджеров расчёта ({len(statuses)}): затихли {risk["dormant"]} '
          f'({num(risk["dormant_rev_year"] / 1e6)} млн драм в год), потеряны {risk["lost"]} '
          f'({num(risk["lost_rev_year"] / 1e6)} млн драм в год), без заказов за год {risk["never"]}; '
          + ', '.join(f'{name} {counts.get(code, 0)}' for code, name in labels))
    print(f'  в выручке модели «было» и «стало» их нет (λ = 0); по истории они давали бы '
          f'≈ {num(hist_week, 0)} драм/нед')
    print()

    # --- критерии ---
    results = []

    freq_bad, pattern_bad = [], []
    for o in out.managers:
        for cid, spec, final in zip(o.customers, o.specs, o.final):
            if len(final) != round(2 * spec.target):
                freq_bad.append(f'{o.code}/{cid}: {len(final)} визитов за цикл при частоте {spec.target:g}')
            if final not in spec.allowed or final in spec.forbidden \
                    or (spec.locked and final != spec.allowed[0]):
                pattern_bad.append(f'{o.code}/{cid}: {pt.pattern_text(final)}')
    total_pairs = sum(len(o.customers) for o in out.managers)
    results.append(('Частоты: визитов за цикл = целевая частота × 2', not freq_bad,
                    f'клиентов {total_pairs}, нарушений {len(freq_bad)}', freq_bad[:5]))
    locked = sum(1 for o in out.managers for s in o.specs if s.locked)
    forbidden = sum(len(s.forbidden) for o in out.managers for s in o.specs)
    results.append(('Шаблоны: допустимые, закреплённые не тронуты, запрещённые не выбраны', not pattern_bad,
                    f'закреплённых {locked}, запрещённых шаблонов {forbidden}, нарушений {len(pattern_bad)}',
                    pattern_bad[:5]))

    time_bad = []
    for m in res['managers']:
        before_ot = overtime_days(m['days_before'], window)                   # в неделю (W = 1)
        after_ot = overtime_days(m['days_after'], window) / pt.CYCLE_WEEKS    # в неделю
        if (before_ot == 0 and after_ot > 0) or after_ot > before_ot:
            time_bad.append(f'{m["code"]}: дней длиннее окна в неделю {before_ot} → {after_ot:g}')
    results.append((f'Время: дней «стало» длиннее {window / 60:g} ч не больше, чем «было»', not time_bad,
                    f'менеджеров с нарушением {len(time_bad)}', time_bad))

    # Р3-9: визиты в нерабочий день (вс) переносятся всегда — «было» с ними не с чем сравнивать: нерабочий
    # день не несёт штрафа слабого дня, а обязательный перенос несёт штраф за изменение. У такого
    # менеджера C_стало сравнивается со стартом поиска — текущим планом, где эти визиты уже на субботе
    # Парк общий (fleet-plan §3): менеджер может стать дороже сам, если парку так выгоднее, — поэтому
    # критерий — по компании (менеджеры + дизель парка); у менеджеров — справочно
    cost_notes = [f'{m["code"]}: свои слагаемые {num(m["before"]["cost"], 0)} → {num(m["after"]["cost"], 0)}'
                  for m in res['managers'] if m['after']['cost'] > m['before']['cost']]
    fleet_txt = (f'; из них дизель парка {num(out.fleet_before, 0)} → {num(out.fleet_after, 0)}'
                 if out.fleet_before is not None else '')
    results.append(('Стоимость: C_стало ≤ C_было компании — менеджеры + дизель парка (быстрая оценка)',
                    out.cost_after <= out.cost_before + 1e-6,
                    f'C компании {num(out.cost_before, 0)} → {num(out.cost_after, 0)} драм/нед{fleet_txt}',
                    cost_notes))

    weak_ok = ta['days_below_min'] <= tb['days_below_min']
    km_ok = ta['manager_km_week'] <= tb['manager_km_week']
    notes = []
    for m in res['managers']:
        b, a = m['before'], m['after']
        if a['manager_km'] > b['manager_km']:
            notes.append(f'{m["code"]}: км +{a["manager_km"] - b["manager_km"]:.1f}/нед, слабых дней '
                         f'{b["days_below_min"]} → {a["days_below_min"]} — поиск меняет пробег на более '
                         f'сильные дни (штраф слабого дня {settings["penalty_weak_day"]:,} драм)'.replace(',', ' '))
        if a['days_below_min'] > b['days_below_min']:
            notes.append(f'{m["code"]}: слабых дней {b["days_below_min"]} → {a["days_below_min"]} в неделю '
                         f'(полная оценка Монте-Карло; поиск видит P(день ≥ минимума) приближённо)')
    results.append(('Итог «стало» не хуже по слабым дням и км менеджеров (полная оценка)', weak_ok and km_ok,
                    f'слабых дней/нед {arrow(tb["days_below_min"], ta["days_below_min"])}, '
                    f'км/нед {arrow(tb["manager_km_week"], ta["manager_km_week"])}', notes))

    # Выручка модели клиента в сезоне s — min(f, λ_s) × средний заказ (p = min(1, λ/f) на визит):
    # частота по продажам — по max λ сезонов, и снижение её не уменьшает ни в один сезон. Потери
    # считаются по клиентам, которым частоту снизило правило «по продажам» (решения владельца — его
    # выбор); итог компании и менеджеров «было → стало» — рядом, допуск — 1 драм (округление).
    seasons = (('зима', 'low', 'revenue_week_low', 'revenue_low'),
               ('лето', 'peak', 'revenue_week_peak', 'revenue_peak'),
               ('год', 'year', 'revenue_week_year', 'revenue_year'))
    models = out.before.models
    cut = sorted({cid for o in out.managers for cid, spec, final in zip(o.customers, o.specs, o.final)
                  if spec.source == 'sales' and len(final) < len(spec.current)})

    def weekly(dem, f: float) -> float:
        return min(f, dem.lam) * dem.mean_revenue if dem.values and f > 0 else 0.0

    loss = {name: sum(weekly(getattr(models[c], attr), res['customers'][str(c)]['freq_current'])
                      - weekly(getattr(models[c], attr), res['customers'][str(c)]['freq_target']) for c in cut)
            for name, attr, _, _ in seasons}
    rev_bad = [f'компания, {name}: {num(tb[key], 0)} → {num(ta[key], 0)}'
               for name, _, key, _ in seasons if ta[key] < tb[key] - 1]
    rev_bad += [f'{m["code"]}, {name}: {num(m["before"][mk], 0)} → {num(m["after"][mk], 0)}'
                for m in res['managers'] for name, _, _, mk in seasons if m['after'][mk] < m['before'][mk] - 1]
    manual = sum(1 for o in out.managers for spec in o.specs if spec.source in ('manual', 'locked'))
    results.append(('Выручка: снижение частоты не теряет выручку ни в один сезон (зима, лето, год)',
                    max(loss.values(), default=0.0) <= 1.0 and (not rev_bad or manual > 0),
                    f'снижений частоты по продажам {len(cut)}, потеря от них в неделю: '
                    + ', '.join(f'{name} {num(v, 0)}' for name, v in loss.items()) + ' драм; итог: '
                    + ', '.join(f'{name} {arrow(tb[key], ta[key], 0)}' for name, _, key, _ in seasons)
                    + (f'; решений владельца по частоте и дням: {manual}' if manual else ''),
                    rev_bad))

    capped = [m['code'] for m in res['managers'] if m['time_capped']]
    results.append((f'Скорость: расчёт по {len(res["managers"])} менеджерам ≤ {TIME_LIMIT_S / 60:g} мин',
                    run_seconds <= TIME_LIMIT_S,
                    f'{run_seconds:.1f} с (поиск {sum(o.stats.seconds for o in out.managers):.1f} с, '
                    f'повтор {runs[1][1]:.1f} с); стоп по времени: {", ".join(capped) or "нет"}', []))

    second = runs[1][0]
    same = [(o.agent_id, o.final) for o in out.managers] == [(o.agent_id, o.final) for o in second.managers]
    results.append(('Повторяемость: второй запуск — те же шаблоны', same,
                    'шаблоны совпадают' if same else 'шаблоны различаются'
                    + (' (был стоп по времени — повторяемость не гарантируется)' if capped else ''), []))

    # §15: потерянным и без заказов — «убрать из маршрута», затихшим — частота ≤ 0.5; решения владельца
    # (оставить, закреплённый шаблон, принятая частота) — его выбор. В выручке модели их нет (λ = 0)
    book = opt.DecisionBook.from_rows(decisions, opt.plan_pairs(snap.plan))
    status_bad = []
    removed = dormant = dormant_ok = 0
    for o in out.managers:
        for cid, spec, final in zip(o.customers, o.specs, o.final):
            s = statuses.get(cid)
            owner = spec.source in ('manual', 'locked') or (o.agent_id, cid) in book.rejected_remove
            if s is None or s.status not in cst.SILENT:
                continue
            if s.status in cst.REMOVABLE:
                if spec.removed and not final:
                    removed += 1
                elif not owner:
                    status_bad.append(f'{o.code}/{cid}: {s.status} — нет предложения «убрать»')
                continue
            dormant += 1
            if pt.pattern_freq(final) <= 0.5 + 1e-9:
                dormant_ok += 1
            elif not owner:
                status_bad.append(f'{o.code}/{cid}: затих — частота «стало» {pt.pattern_freq(final):g}')
    silent_rev = sum(models[c].draw(season).expected_revenue
                     for c in silent for season in ('low', 'year', 'peak'))
    results.append(('Статус клиента (§15): потерянным и без заказов — «убрать», затихшим — ≤ раз в 2 недели, '
                    'в выручке их нет', not status_bad and silent_rev == 0,
                    f'предложений «убрать» {removed}, затихших с частотой ≤ 0,5 — {dormant_ok} из {dormant}; '
                    f'выручка модели у них {num(silent_rev, 0)} драм/нед', status_bad[:8]))

    for title, ok, detail, lines in results:
        print(f'{status(ok):<12} {title}: {detail}')
        for line in lines:
            print(f'             {line}')
    print()

    changes = [(m['code'], ch) for m in res['managers'] for ch in m['changes']]
    samples = changes[:3]
    samples += [x for x in changes if x[1]['type'] == 'remove'][:2]                    # потерян, без заказов
    samples += [x for x in changes if cst.WIN_BACK_TEXT in (x[1]['reason'] or '')][:2]  # затих
    if samples:
        print('Примеры предложений (эффект — этого изменения к текущему плану, в неделю):')
        for code, ch in samples:
            e = ch['effect']
            name = res['customers'][str(ch['customer_id'])]['name']
            print(f'  {code} · {name}: «{ch["from"]["text"]}» → «{ch["to"]["text"]}»'
                  + (f' ({ch["reason"]})' if ch['reason'] else '')
                  + f'; км {e["manager_km_week"]:+.1f}, грузовик '
                  + (f'{e["truck_km_week"]:+.1f}' if e['truck_km_week'] is not None else '—')
                  + f' км, слабых дней {e["weak_days_week"]:+.2f}, минут {e["minutes_week"]:+d}')
    fleet_results = check_fleet(out, bundle, 'А', run_seconds)
    results += fleet_results
    for title, ok, detail, lines in fleet_results:
        print(f'{status(ok):<12} {title}: {detail}')
        for line in lines:
            print(f'             {line}')
    passed = sum(1 for _, ok, _, _ in results if ok)
    verdict = 'ПРОЙДЕНО' if passed == len(results) else 'НЕ ПРОЙДЕНО'
    print(f'ИТОГ режима А: {verdict} по {passed} из {len(results)} критериев')
    print()
    if out.before.fleet is not None:
        reality_check(snap, out.before)
        print()
    results_b = check_transfer(snap, bundle, calib, decisions, out, window)
    passed_b = sum(1 for _, ok, _, _ in results_b if ok)
    verdict_b = 'ПРОЙДЕНО' if passed_b == len(results_b) else 'НЕ ПРОЙДЕНО'
    print(f'ИТОГ режима Б: {verdict_b} по {passed_b} из {len(results_b)} критериев')
    return 0 if verdict == verdict_b == 'ПРОЙДЕНО' else 1


KEY_MANAGERS = ('A002/10', 'A004/11')   # мало клиентов: зимой не дотягивают до 100 000 в 1–2 днях


def check_transfer(snap, bundle, calib, decisions, out_a, window: float) -> list:
    """Режим Б на том же снимке и настройках: таблица «было → стало», передачи, балансы, критерии §7."""
    runs = []
    for _ in range(2):
        t0 = time.perf_counter()
        out = opt.run_optimization(snap, bundle, calib, decisions, {**PARAMS, 'mode': 'transfer'})
        runs.append((out, time.perf_counter() - t0))
    out, run_seconds = runs[0]
    res, res_a = out.result, out_a.result
    tr = res['transfers']
    print('Режим Б — передача магазинов между менеджерами (тот же снимок и настройки)')
    print(f'  плата за передачу {num(tr["penalty_week"], 0)} драм/нед, радиус «клиент рядом» {tr["radius_km"]} км; '
          f'кандидатов на передачу {tr["candidates"]}; принятых владельцем передач {tr["accepted_fixed"]}')
    print()
    header = (f'{"Менеджер":<9} {"визитов/нед":>15} {"слабых дн./нед":>15} {"слаб. реж. А":>12} {"км/нед":>17} '
              f'{"км реж. А":>10} {"отдаёт":>7} {"получает":>8} {"выручка ушла / пришла, драм/мес":>34} '
              f'{"долг ушёл / пришёл":>22}')
    print(header)
    print('-' * len(header))
    by_a = {m['agent_id']: m for m in res_a['managers']}
    for m in res['managers']:
        b, a, ma, bal = m['before'], m['after'], by_a[m['agent_id']], m['balance']
        g, r = bal['given'], bal['received']
        print(f'{m["code"]:<9} {arrow(b["visits"], a["visits"]):>15} '
              f'{arrow(b["days_below_min"], a["days_below_min"]):>15} '
              f'{num(ma["after"]["days_below_min"]):>12} {arrow(b["manager_km"], a["manager_km"]):>17} '
              f'{num(ma["after"]["manager_km"]):>10} {g["stores"]:>7} {r["stores"]:>8} '
              f'{num(g["revenue_month"], 0) + " / " + num(r["revenue_month"], 0):>34} '
              f'{num(g["debt"], 0) + " / " + num(r["debt"], 0):>22}')
    tb, ta, taa = res['before'], res['after'], res_a['after']
    print('-' * len(header))
    print(f'{"Компания":<9} {arrow(tb["visits_week"], ta["visits_week"]):>15} '
          f'{arrow(tb["days_below_min"], ta["days_below_min"]):>15} {num(taa["days_below_min"]):>12} '
          f'{arrow(tb["manager_km_week"], ta["manager_km_week"]):>17} {num(taa["manager_km_week"]):>10} '
          f'{tr["count"]:>7} {tr["count"]:>8} {num(tr["revenue_month"], 0):>34} {num(tr["debt"], 0):>22}')
    print(f'  выручка в неделю (модель): зима {arrow(tb["revenue_week_low"], ta["revenue_week_low"], 0)}, '
          f'лето {arrow(tb["revenue_week_peak"], ta["revenue_week_peak"], 0)}, '
          f'год {arrow(tb["revenue_week_year"], ta["revenue_week_year"], 0)}; '
          f'визитов в неделю — режим А {num(taa["visits_week"])}')
    cost_a = out_a.cost_after          # компания режима А: менеджеры + дизель парка
    print(f'  C компании (быстрая оценка, драм/нед): итог режима А {num(cost_a, 0)}, '
          f'старт режима Б (то же + принятые передачи) {num(out.transfer.cost_start, 0)}, итог режима Б '
          f'{num(out.transfer.cost_end, 0)}; поиск передач {out.transfer.seconds:.1f} с '
          f'(ходов {out.transfer.accepted}, '
          f'внутри менеджеров {out.transfer.intra_accepted}, возмущений {out.transfer.perturbations})')
    key = {m['code']: m for m in res['managers']}
    for code in KEY_MANAGERS:
        if code in key:
            mb, ma = key[code], by_a.get(key[code]['agent_id'])
            print(f'  {code}: слабых зимних дней в неделю — сейчас {num(mb["before"]["days_below_min"])}, режим А '
                  f'{num(ma["after"]["days_below_min"])}, режим Б {num(mb["after"]["days_below_min"])} '
                  f'(получает {mb["balance"]["received"]["stores"]}, '
                  f'отдаёт {mb["balance"]["given"]["stores"]} магазинов)')
    moves = [(m['code'], ch) for m in res['managers'] for ch in m['changes'] if ch['type'] == 'transfer']
    codes = {m['agent_id']: m['code'] for m in res['managers']}
    if moves:
        print('  Передачи (эффект — этой передачи к текущему плану, по обоим менеджерам, в неделю):')
        for code, ch in moves:
            e, ef, et = ch['effect'], ch['effect_from'], ch['effect_to']
            name = res['customers'][str(ch['customer_id'])]['name']
            print(f'    {code} → {codes[ch["to_agent"]]} · {name}: «{ch["from"]["text"]}» → «{ch["to"]["text"]}» '
                  f'({ch["reason"]}); км {ef["manager_km_week"]:+.1f} / {et["manager_km_week"]:+.1f}, слабых дней '
                  f'{e["weak_days_week"]:+.2f}, минут {e["minutes_week"]:+d}; выручка {num(ch["revenue_month"], 0)} '
                  f'драм/мес, долг {num(ch["debt"], 0)}')
    print()

    results = []
    freq_bad, pattern_bad, locked_moved, shared_moved = [], [], [], []
    owners = Counter(c for a in out.before.included_ids for c in opt.plan_pairs(snap.plan).get(a, {}))
    book = opt.DecisionBook.from_rows(decisions, opt.plan_pairs(snap.plan))
    for o in out.managers:
        for cid, spec, final in zip(o.customers, o.specs, o.final):
            fixed = book.accepted_transfer.get((o.agent_id, cid))
            if cid in o.moved_to:
                if spec.locked:
                    locked_moved.append(f'{o.code}/{cid}')
                if owners[cid] > 1:
                    shared_moved.append(f'{o.code}/{cid}')
                if fixed is not None and fixed == (o.moved_to[cid], final):
                    continue                    # принятая владельцем передача — в принятых днях
            if len(final) != round(2 * spec.target):
                freq_bad.append(f'{o.code}/{cid}: {len(final)} визитов за цикл при частоте {spec.target:g}')
            if final not in spec.allowed or final in spec.forbidden or (spec.locked and final != spec.allowed[0]):
                pattern_bad.append(f'{o.code}/{cid}: {pt.pattern_text(final)}')
    moved = sum(len(o.moved_to) for o in out.managers)
    results.append(('Б: частоты и шаблоны — как в режиме А, и у переданных', not freq_bad and not pattern_bad,
                    f'передано {moved}, нарушений частоты {len(freq_bad)}, шаблонов {len(pattern_bad)}',
                    (freq_bad + pattern_bad)[:6]))
    time_bad = []
    for m in res['managers']:
        before_ot = overtime_days(m['days_before'], window)
        after_ot = overtime_days(m['days_after'], window) / pt.CYCLE_WEEKS
        if (before_ot == 0 and after_ot > 0) or after_ot > before_ot:
            time_bad.append(f'{m["code"]}: дней длиннее окна в неделю {before_ot} → {after_ot:g}')
    results.append((f'Б: время — дней «стало» длиннее {window / 60:g} ч не больше, чем «было»', not time_bad,
                    f'менеджеров с нарушением {len(time_bad)}', time_bad))
    cost_ok = out.transfer.cost_end <= out.transfer.cost_start + 1e-6 and \
        (tr['accepted_fixed'] or out.transfer.cost_end <= cost_a + 1e-6)
    results.append(('Б: стоимость компании ≤ режима А (быстрая оценка, с дизелем парка и платой за передачи)', cost_ok,
                    f'C режима А {num(cost_a, 0)} → режима Б {num(out.transfer.cost_end, 0)} драм/нед', []))
    results.append(('Б: закреплённые и клиенты нескольких менеджеров не передаются',
                    not locked_moved and not shared_moved,
                    f'закреплённых передано {len(locked_moved)}, общих {len(shared_moved)}',
                    (locked_moved + shared_moved)[:6]))
    bal_bad = []
    for k in ('stores', 'revenue_month', 'debt'):
        given = sum(m['balance']['given'][k] for m in res['managers'])
        got = sum(m['balance']['received'][k] for m in res['managers'])
        if given != got:
            bal_bad.append(f'{k}: отдано {given}, получено {got}')
    for m in res['managers']:
        mine = [c for c in m['changes'] if c['type'] == 'transfer']
        if m['balance']['given']['stores'] != len(mine) or \
                abs(m['balance']['given']['revenue_month'] - sum(c['revenue_month'] for c in mine)) > len(mine):
            bal_bad.append(f'{m["code"]}: «отдаёт» не равно сумме его передач')
    results.append(('Б: балансы сходятся (отдано = получено; «отдаёт» = сумма передач менеджера)', not bal_bad,
                    f'передач {tr["count"]}, выручка {num(tr["revenue_month"], 0)} драм/мес, долг '
                    f'{num(tr["debt"], 0)} драм', bal_bad))
    second = runs[1][0]
    same = ([(o.agent_id, o.final, o.moved_to) for o in out.managers]
            == [(o.agent_id, o.final, o.moved_to) for o in second.managers])
    capped = res['transfers']['time_capped'] or any(m['time_capped'] for m in res['managers'])
    results.append(('Б: повторяемость — второй запуск даёт те же дни и передачи', same,
                    'совпадают' if same else 'различаются' + (' (был стоп по времени)' if capped else ''), []))
    results.append((f'Б: скорость — расчёт ≤ {TIME_LIMIT_S / 60:g} мин', run_seconds <= TIME_LIMIT_S,
                    f'{run_seconds:.1f} с (повтор {runs[1][1]:.1f} с); '
                    f'стоп по времени: {"да" if capped else "нет"}', []))
    results += check_fleet(out, bundle, 'Б', run_seconds)
    for title, ok, detail, lines in results:
        print(f'{status(ok):<12} {title}: {detail}')
        for line in lines:
            print(f'             {line}')
    return results


def _fleet_week(pe) -> dict:
    return pe.fleet.week() if pe.fleet is not None else {}


def check_fleet(out, bundle, mode: str, run_seconds: float) -> list:
    """Парк машин «было → стало» (полная оценка) и проверка рейсов «стало» заново по пробам: тоннаж
    машины не превышен, машина в рабочий день (кроме рейсов «за пределами дня» — нехватка машин)."""
    if out.before.fleet is None:
        print(f'  Парк машин (режим {mode}): не посчитан — нет склада или машин с тоннажем и расходом')
        return []
    s = bundle.settings
    fb, fa = _fleet_week(out.before), _fleet_week(out.after)
    diesel = s['fuel_price_diesel']
    print(f'Парк машин, режим {mode} (полная оценка, год; по заказам всех менеджеров в расчёте):')
    print(f'  дизель парка {arrow(fb["liters"], fa["liters"])} л/нед'
          + (f' ({arrow(fb["liters"] * diesel, fa["liters"] * diesel, 0)} драм)' if diesel else '')
          + f'; км парка {arrow(fb["km"], fa["km"])}; рейсов в неделю {arrow(fb["trips"], fa["trips"])}, '
          f'в день {arrow(fb["trips_per_day"], fa["trips_per_day"])}; машино-часов {arrow(fb["hours"], fa["hours"])}')
    print(f'  загрузка рейса {arrow(fb["load_pct"], fa["load_pct"])}% (в пик {arrow(fb["load_pct_peak"], fa["load_pct_peak"])}%); '
          f'дней с нехваткой машин в пик в неделю {arrow(fb["days_short"], fa["days_short"])}; выручка рейса '
          f'{arrow(fb["trip_revenue"], fa["trip_revenue"], 0)} драм, рейсов < {num(s["min_trip_revenue"], 0)} — '
          f'{arrow(100 * (fb["poor_trip_share"] or 0), 100 * (fa["poor_trip_share"] or 0), 0)}%')
    norms, tn = out.after.norms, fl.TruckNorms.from_settings(s)
    trucks = out.after.fleet.trucks
    cap = {t.car_code: t.capacity_kg for t in trucks}
    over_kg = over_time = trips = extra = 0
    by_day = defaultdict(list)
    for me in out.after.evals:
        if me.included:
            for r in me.days:
                by_day[fl.delivery_key(r.day.week, r.day.weekday, 2)].extend(
                    fl.DeliveryVisit(v.customer_id, r.day.weekday, v.point, v.year, v.peak)
                    for v in r.stops if v.point is not None)
    for key, visits in sorted(by_day.items()):
        visits = sorted(visits, key=lambda v: (v.customer_id, v.weekday, v.point))
        points = sorted({v.point for v in visits})
        node = {p: i + 1 for i, p in enumerate(points)}
        d, m = fl._matrices(points, bundle.depot, norms)
        for stops in fl._samples(visits, node, 'year', fl.FLEET_SAMPLES, tn):
            used = Counter()
            for t in fl.plan_trips(stops, d, m, trucks, tn):
                trips += 1
                extra += t.extra
                over_kg += t.kg > cap[t.truck] + 1e-6
                if not t.extra:
                    used[t.truck] += t.minutes
            over_time += sum(1 for v in used.values() if v > tn.work_minutes + 1e-6)
    print('  по машинам в среднем за день доставки «стало»:')
    agg = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for dday in out.after.fleet.days:
        for t in dday.trucks:
            a = agg[t.car_code]
            a[0] += t.trips; a[1] += t.kg; a[2] += t.km; a[3] += t.liters
    n_days = len(out.after.fleet.days) or 1
    for code, (tr_, kg, km, lit) in sorted(agg.items()):
        print(f'    {code}: рейсов {tr_ / n_days:.1f}, {kg / n_days:,.0f} кг, {km / n_days:.0f} км, {lit / n_days:.1f} л'
              .replace(',', ' ') + f' (загрузка {kg / max(tr_, 1e-9) / cap[code] * 100:.0f}%)')
    return [
        (f'Парк ({mode}): тоннаж машин не превышен, рабочий день машин соблюдён (рейсы «стало» заново, '
         f'{fl.FLEET_SAMPLES} проб на день)', over_kg == 0 and over_time == 0,
         f'рейсов {trips}, сверх тоннажа {over_kg}, машин с днём длиннее нормы {over_time}; рейсов за пределами дня '
         f'(нехватка машин) {extra} ({100 * extra / max(trips, 1):.1f}%)', []),
        (f'Парк ({mode}): дизель парка «стало» не больше «было»', fa['liters'] <= fb['liters'] + 1e-6,
         f'{arrow(fb["liters"], fa["liters"])} л/нед (полная оценка); расчёт {run_seconds:.1f} с', []),
    ]


def reality_check(snap, before) -> None:
    """Справочно: модель «как сейчас» (рейсов и кг на день доставки, год) против факта ERP за последние
    4 недели — сколько машин выезжало и сколько кг везли по SALES.fDELIVERYCAR (только SELECT)."""
    until = snap.today
    since = until - timedelta(days=28)
    conn = erp.connect(app_v2.db.connection_string)
    try:
        rows = erp.car_days(conn, since, until)
    finally:
        erp.close_quietly(conn)
    by_date = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        if r.kg > 0:
            x = by_date[r.day]
            x[0] += 1
            x[1] += r.kg
            x[2] += r.docs
    model = defaultdict(list)
    for d in before.fleet.days:
        model[d.weekday].append(d)
    print(f'Сверка с ERP (справочно): модель «как сейчас» (год) против факта {since:%d.%m}–{until - timedelta(days=1):%d.%m} '
          f'по SALES.fDELIVERYCAR')
    print(f'  {"день":<5} {"факт: машин":>12} {"факт: кг":>10} {"факт: док.":>10} {"модель: рейсов":>15} '
          f'{"модель: заказов":>16} {"модель: кг":>11} {"модель: кг в пик":>17}')
    fact_wd = defaultdict(list)
    for day, x in by_date.items():
        fact_wd[day.isoweekday()].append(x)
    for wd in range(1, 8):
        f = fact_wd.get(wd, [])
        md = model.get(wd, [])
        if not f and not md:
            continue
        print(f'  {pt.DAY_SHORT[wd]:<5} {num(fmean(x[0] for x in f) if f else None):>12} '
              f'{num(fmean(x[1] for x in f) if f else None, 0):>10} {num(fmean(x[2] for x in f) if f else None, 0):>10} '
              f'{num(fmean(d.trips for d in md) if md else None):>15} {num(fmean(d.orders for d in md) if md else None):>16} '
              f'{num(fmean(d.kg for d in md) if md else None, 0):>11} {num(fmean(d.kg_peak for d in md) if md else None, 0):>17}')
    days = sorted(by_date)
    if days:
        print(f'  итого факт: {len(days)} дней с развозом, в среднем {fmean(by_date[d][0] for d in days):.1f} машин и '
              f'{fmean(by_date[d][1] for d in days):,.0f} кг в день'.replace(',', ' ')
              + f'; модель «как сейчас»: {before.fleet.week()["trips_per_day"]:.1f} рейсов и '
              f'{before.fleet.week()["kg_per_day"]:,.0f} кг в день доставки'.replace(',', ' '))


if __name__ == '__main__':
    sys.exit(main())

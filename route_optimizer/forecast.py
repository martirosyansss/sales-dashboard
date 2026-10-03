"""Проверка спроса на завершённом периоде, который не входит в обучение.

Модель визита остаётся p=min(1, λ/f). Проверка измеряет её ограничения на клиентах
текущего плана; она не подменяет прогноз фактом и не подгоняет параметры под тест.
Чистая логика: ERP и SQLite здесь не открываются.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Collection

from . import demand as dm

TRAIN_WEEKS = 12
TEST_WEEKS = 4
MIN_LAG_DAYS = 3
TOLERANCE = 0.10


def revenue_validation(snap, included: Collection[int]) -> dict[str, Any]:
    """Четыре завершённые недели: спрос обучен только на предшествующих 12 неделях.

    Сезонная поправка берётся из соответствующих окон прошлого года. В исторических
    заказах нет координат или строк подключения. При недостатке истории статус явный.
    """
    orders = getattr(snap, 'validation_orders', ())
    if not orders:
        return {'status': 'unavailable', 'ok': False, 'reason': 'Нет истории для независимой проверки',
                'managers': [], 'passed': 0, 'total': 0, 'tolerance_pct': 100*TOLERANCE}
    today = snap.today
    last_sat = today - timedelta(days=(today.weekday()-5) % 7 or 7)
    if (today-last_sat).days < MIN_LAG_DAYS:
        last_sat -= timedelta(days=7)
    test_start = last_sat - timedelta(days=7*TEST_WEEKS-2)
    test_end = last_sat + timedelta(days=1)
    train_start = test_start - timedelta(weeks=TRAIN_WEEKS)
    year = timedelta(days=364)

    def in_period(day, start, end):
        return start <= day < end and day.weekday() < 6

    # Один проход; продажи тестового периода никогда не попадают в модели клиентов.
    training = defaultdict(list)
    actual = defaultdict(list)
    last_year_train = last_year_test = 0.0
    for order in orders:
        if train_start <= order.date < test_start:
            training[order.customer_id].append(order)
        if in_period(order.date, test_start, test_end):
            actual[order.agent_id].append(order)
        if in_period(order.date, train_start-year, test_start-year):
            last_year_train += order.revenue
        if in_period(order.date, test_start-year, test_end-year):
            last_year_test += order.revenue
    scale = ((last_year_test/TEST_WEEKS)/(last_year_train/TRAIN_WEEKS)
             if last_year_train > 0 and last_year_test > 0 else 1.0)
    frequency = snap.plan.visits_per_week_among(included)
    models = {cid: dm.window_demand(training.get(cid,()),[(train_start,test_start)],
                                   snap.first_order.get(cid),test_start,scale=scale)
              for cid in snap.plan.customer_ids}
    rows = []
    for agent in sorted(included):
        days = [d for d in snap.plan.days_of(agent) if d.weekday < 7]
        if not days:
            continue
        customers = {v.customer_id for d in days for v in d.visits}
        predicted = sum(dm.visit_probability(models[v.customer_id].lam,frequency.get(v.customer_id,0))
                        *models[v.customer_id].mean_revenue/snap.plan.cycle_weeks
                        for d in days for v in d.visits)
        fact = sum(o.revenue for o in actual[agent] if o.customer_id in customers)/TEST_WEEKS
        error = predicted/fact-1 if fact > 0 else None
        supported = fact > 0
        rows.append({'agent_id':agent,'code':snap.agents[agent].code if agent in snap.agents else str(agent),
                     'model_week':round(predicted),'fact_week':round(fact),
                     'error_pct':round(error*100,1) if error is not None else None,
                     'ok':supported and abs(error)<=TOLERANCE,'supported':supported})
    supported = [r for r in rows if r['supported']]
    passed = sum(r['ok'] for r in supported)
    return {'status':'checked' if supported else 'unavailable','ok':bool(supported) and passed==len(supported),
            'train_start':train_start.isoformat(),'train_end':test_start.isoformat(),
            'test_start':test_start.isoformat(),'test_end':test_end.isoformat(),
            'tolerance_pct':100*TOLERANCE,'passed':passed,'total':len(supported),
            'season_scale':round(scale,4),'season_supported':last_year_train>0 and last_year_test>0,
            'managers':rows,'scope':'Выручка клиентов исходного плана, 4 завершённые недели'}
